"""
#60 (13.10.2026): выплата исполнителю формируется автоматически при оплате сделки — сумма сразу «к выплате»,
блогер заявку не подаёт; реестр выплат: блокировка без реквизитов и без ЭСФ, отказ с причиной, повторная выплата,
снимок реквизитов при их подтверждении; итоги для бухгалтерии (metrics).
"""
from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.billing import metrics
from apps.billing.models import EInvoice, PayoutRequisites, Transaction, Wallet, WithdrawalRequest
from apps.billing.payout_requisites import approve_payout_requisites
from apps.billing.services import BillingService
from apps.campaigns.models import Campaign
from apps.campaigns.models import Response as CampaignResponse
from apps.campaigns.testing import CARD_FIELDS, CLAIM_FIELDS, deal_from_response
from apps.deals import services as t
from apps.deals.models import Deal
from apps.notifications.models import Notification
from apps.platforms.models import Platform
from apps.profiles.models import BloggerProfile
from apps.users.models import User

S = Deal.Status
D = Decimal
_n = 0


def _file(name="s.png"):
    return SimpleUploadedFile(name, b"\x89PNG", content_type="image/png")


def _user(email, role=User.Role.BLOGGER, **extra):
    return User.objects.create_user(email=email, password="pass1234", role=role, status=User.Status.ACTIVE, **extra)


def approved_card(user, number="8600123456789012"):
    return PayoutRequisites.objects.create(
        user=user, requisite_type=PayoutRequisites.RequisiteType.CARD, card_number=number,
        card_holder_name="Test Bloggerov", status=PayoutRequisites.Status.APPROVED,
    )


class DealPayoutBase(TestCase):
    """Сделка по отклику с публикацией и оплатой по сроку — общий каркас (как в tests_retention_payout)."""

    def setUp(self):
        self.today = timezone.localdate()
        self.adv = _user("adv@test.com", User.Role.ADVERTISER)
        Wallet.objects.filter(user=self.adv).update(available_balance=D("1000000"))
        self.blogger = _user("bl@test.com")
        self.staff = _user("staff@test.com", User.Role.ADVERTISER, is_staff=True)
        self.campaign = Campaign.objects.create(
            advertiser=self.adv, name="Кампания", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=D("100000"), budget=D("1000000"), status=Campaign.Status.ACTIVE,
            start_date=self.today, end_date=self.today + timedelta(days=40), approval_required=False,
            min_retention_days=1, evidence_required=["screenshot"], **CARD_FIELDS,
        )

    def _deal(self, blogger=None):
        global _n
        _n += 1
        blogger = blogger or self.blogger
        platform = Platform.objects.create(
            blogger=blogger, social_type=Platform.SocialType.INSTAGRAM, url=f"https://instagram.com/dp{_n}",
            subscribers=12000, price_post=D("50000"), status=Platform.Status.APPROVED,
        )
        resp = CampaignResponse.objects.create(campaign=self.campaign, blogger=blogger, platform=platform,
                                               content_type="post", proposed_price=D("100000"))
        return deal_from_response(resp, self.adv, publication_date=self.today)

    def _paid(self, blogger=None):
        """Сделка опубликована и оплачена по сроку → COMPLETED."""
        deal = self._deal(blogger)
        t.submit_publication(deal.pk, deal.blogger, "https://instagram.com/p/1", {"screenshot": [_file()]})
        Deal.objects.filter(pk=deal.pk).update(publication_at=timezone.now() - timedelta(days=10))
        self.assertEqual(t.auto_complete_overdue(), 1)
        deal.refresh_from_db()
        return deal

    def _wallet(self, user=None):
        return Wallet.objects.get(user=user or self.blogger)


class AutoPayoutTest(DealPayoutBase):
    def test_payment_creates_payout_and_moves_to_on_withdrawal(self):
        deal = self._paid()
        self.assertEqual(deal.status, S.COMPLETED)
        wallet = self._wallet()
        self.assertEqual((wallet.available_balance, wallet.on_withdrawal), (D("0"), D("100000")))
        payout = WithdrawalRequest.objects.get(deal=deal)
        self.assertEqual((payout.kind, payout.status, payout.amount),
                         (WithdrawalRequest.Kind.DEAL, WithdrawalRequest.Status.PENDING, D("100000")))
        self.assertEqual(deal.payout, payout)
        withdrawal = Transaction.objects.get(wallet=wallet, type=Transaction.Type.WITHDRAWAL)
        self.assertEqual((withdrawal.amount, withdrawal.deal_id), (D("-100000"), deal.pk))
        note = Notification.objects.get(user=self.blogger, title="Выплата поставлена в реестр")
        self.assertIn("100 000", note.body)

    def test_demo_blogger_gets_payout_too(self):
        demo = _user("demo@test.com", is_demo=True)
        deal = self._paid(demo)
        self.assertTrue(WithdrawalRequest.objects.filter(deal=deal, blogger=demo).exists())
        self.assertEqual(self._wallet(demo).available_balance, D("0"))

    def test_claim_split_creates_payout_on_part(self):
        deal = self._deal()
        t.submit_publication(deal.pk, self.blogger, "https://instagram.com/p/1", {"screenshot": [_file()]})
        t.open_claim(deal.pk, self.adv, **CLAIM_FIELDS)
        t.resolve_claim(deal.pk, self.staff, "split", "Частично", "40 000")
        payout = WithdrawalRequest.objects.get(deal=deal)
        self.assertEqual((payout.amount, self._wallet().on_withdrawal), (D("40000"), D("40000")))

    def test_snapshot_taken_at_creation_when_requisites_exist(self):
        approved_card(self.blogger, "1111222233334444")
        deal = self._paid()
        self.assertEqual(WithdrawalRequest.objects.get(deal=deal).requisites["card_number"], "1111222233334444")

    def test_only_one_open_payout_per_deal(self):
        deal = self._paid()
        with self.assertRaises(IntegrityError), transaction.atomic():
            WithdrawalRequest.objects.create(blogger=self.blogger, deal=deal, kind="deal", amount=D("1"))
        with self.assertRaisesMessage(ValueError, "уже есть выплата"):
            BillingService.reissue_deal_payout(deal)


class PayoutProcessingTest(DealPayoutBase):
    def test_blocked_without_requisites(self):
        deal = self._paid()
        payout = WithdrawalRequest.objects.get(deal=deal)
        self.assertEqual(payout.blocked_reasons, ["нет подтверждённых реквизитов"])
        with self.assertRaisesMessage(ValueError, "нет подтверждённых реквизитов"):
            BillingService.complete_withdrawal(payout, comment="п/п 1")
        self.assertEqual(WithdrawalRequest.objects.get(pk=payout.pk).status, WithdrawalRequest.Status.PENDING)

    def test_approving_requisites_fills_snapshot_and_unblocks(self):
        deal = self._paid()
        app = PayoutRequisites.objects.create(
            user=self.blogger, requisite_type=PayoutRequisites.RequisiteType.CARD, card_number="8600000000001234",
            card_holder_name="Test Bloggerov",
        )
        approve_payout_requisites(app.pk, self.staff)
        payout = WithdrawalRequest.objects.get(deal=deal)
        self.assertEqual(payout.requisites["card_number"], "8600000000001234")
        self.assertTrue(payout.is_ready)
        BillingService.complete_withdrawal(payout, comment="п/п 7")
        wallet = self._wallet()
        self.assertEqual(wallet.on_withdrawal, D("0"))
        self.assertEqual(Transaction.objects.get(wallet=wallet, type=Transaction.Type.PAYOUT).deal_id, deal.pk)
        self.assertEqual(metrics.blogger_paid_out(self.blogger), D("100000"))
        # ЭСФ платформы на комиссию — к выставлению
        platform = EInvoice.objects.get(deal=deal, kind=EInvoice.Kind.PLATFORM)
        self.assertEqual((platform.status, platform.amount), (EInvoice.Status.TO_ISSUE, D("16000")))

    def test_snapshot_from_current_requisites_at_processing(self):
        deal = self._paid()
        approved_card(self.blogger, "9999888877776666")
        payout = WithdrawalRequest.objects.get(deal=deal)
        self.assertEqual(payout.requisites, {})
        BillingService.complete_withdrawal(payout, comment="п/п 2")
        payout.refresh_from_db()
        self.assertEqual(payout.requisites["card_number"], "9999888877776666")

    def test_blocked_until_einvoice_confirmed_for_ip(self):
        BloggerProfile.objects.filter(user=self.blogger).update(category=BloggerProfile.Category.IP,
                                                                 is_ip_confirmed=True)
        PayoutRequisites.objects.create(
            user=self.blogger, requisite_type=PayoutRequisites.RequisiteType.ACCOUNT, account_number="2020",
            bank_mfo="00014", bank_name="Банк", status=PayoutRequisites.Status.APPROVED,
        )
        deal = self._paid()
        payout = WithdrawalRequest.objects.get(deal=deal)
        self.assertEqual(payout.blocked_reasons, ["ждёт ЭСФ исполнителя"])
        with self.assertRaisesMessage(ValueError, "ждёт ЭСФ исполнителя"):
            BillingService.complete_withdrawal(payout, comment="п/п 3")
        from apps.billing import esf

        einvoice = EInvoice.objects.get(deal=deal, kind=EInvoice.Kind.PERFORMER)
        esf.set_status(einvoice.pk, self.staff, status=EInvoice.Status.CONFIRMED, number="ЭСФ-1")
        payout = WithdrawalRequest.objects.get(pk=payout.pk)
        self.assertTrue(payout.is_ready)
        BillingService.complete_withdrawal(payout, comment="п/п 3")
        self.assertEqual(self._wallet().on_withdrawal, D("0"))

    def test_reject_requires_reason_and_refunds_then_reissue(self):
        approved_card(self.blogger)
        deal = self._paid()
        payout = WithdrawalRequest.objects.get(deal=deal)
        with self.assertRaisesMessage(ValueError, "причину"):
            BillingService.reject_withdrawal(payout, comment="")
        BillingService.reject_withdrawal(payout, comment="Банк вернул перевод")
        wallet = self._wallet()
        self.assertEqual((wallet.available_balance, wallet.on_withdrawal), (D("100000"), D("0")))
        self.assertEqual(Transaction.objects.get(wallet=wallet, type=Transaction.Type.REFUND).deal_id, deal.pk)
        second = BillingService.reissue_deal_payout(deal)
        self.assertEqual((second.amount, second.status), (D("100000"), WithdrawalRequest.Status.PENDING))
        wallet = self._wallet()
        self.assertEqual((wallet.available_balance, wallet.on_withdrawal), (D("0"), D("100000")))
        self.assertEqual(deal.payouts.count(), 2)
        self.assertEqual(deal.payout, second)

    def test_legacy_manual_request_not_blocked_by_einvoice(self):
        Wallet.objects.filter(user=self.blogger).update(available_balance=D("50000"))
        wr = WithdrawalRequest.objects.create(blogger=self.blogger, amount=D("50000"),
                                              requisites={"type": "card", "card_number": "8600"})
        BillingService.process_withdrawal(wr)
        self.assertEqual(wr.blocked_reasons, [])
        BillingService.complete_withdrawal(wr, comment="")
        self.assertEqual(self._wallet().on_withdrawal, D("0"))


class MetricsTotalsTest(DealPayoutBase):
    def test_period_totals_and_commission(self):
        approved_card(self.blogger)
        deal = self._paid()
        BillingService.complete_withdrawal(WithdrawalRequest.objects.get(deal=deal), comment="п/п 9")
        self.assertEqual(metrics.deal_commission(deal), D("16000"))
        start, end = metrics.period_bounds(self.today, self.today)
        totals = metrics.period_totals(start, end)
        self.assertEqual(totals["reserves"], D("116000"))
        self.assertEqual(totals["payments"], D("116000"))
        self.assertEqual(totals["earnings"], D("100000"))
        self.assertEqual(totals["commission"], D("16000"))
        self.assertEqual(totals["withdrawals"], D("100000"))
        self.assertEqual(totals["payouts"], D("100000"))
        days = metrics.daily_totals(start, end)
        self.assertEqual(len(days), 1)
        self.assertEqual((days[0]["day"], days[0]["payouts"]), (self.today, D("100000")))
        self.assertEqual(metrics.period_totals(*metrics.period_bounds(self.today - timedelta(days=3),
                                                                      self.today - timedelta(days=1)))["count"], 0)

    def test_daily_totals_split_by_local_day(self):
        wallet = Wallet.objects.get(user=self.adv)
        tx = Transaction.objects.create(wallet=wallet, type=Transaction.Type.DEPOSIT, amount=D("1000"),
                                        balance_after=D("1000"))
        yesterday = timezone.now() - timedelta(days=1)
        Transaction.objects.filter(pk=tx.pk).update(created_at=yesterday)
        Transaction.objects.create(wallet=wallet, type=Transaction.Type.DEPOSIT, amount=D("2000"),
                                   balance_after=D("3000"))
        start, end = metrics.period_bounds(self.today - timedelta(days=1), self.today)
        days = metrics.daily_totals(start, end)
        self.assertEqual([(d["day"], d["deposits"]) for d in days],
                         [(timezone.localdate(yesterday), D("1000")), (self.today, D("2000"))])
