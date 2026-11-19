"""
Тарифы рекламодателей (таблица бизнеса 19.11.2026): комиссия по тарифу сверх стоимости услуг (ПР 7.1, 7.3), лимит
кампаний в месяц и минимальный бюджет при отправке на модерацию; тариф назначает сотрудник, особую ставку согласует
директор; ставка фиксируется при направлении оферты (ПР 7.4); плата исполнителя по умолчанию — 10%.
"""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.billing import metrics
from apps.billing.models import SpecialTariff, Tariff, TariffAssignment, Transaction, Wallet
from apps.billing.services import BillingService
from apps.billing.tariffs import assign_tariff, commission_percent_for, contractor_fee_percent_for, tariff_for
from apps.campaigns.models import Campaign, DirectOffer
from apps.campaigns.models import Response as CampaignResponse
from apps.campaigns.services import accept_direct_offer, accept_response
from apps.campaigns.testing import CARD_FIELDS, blogger_accepts, publication_day
from apps.campaigns.validation import moderation_error, moderation_missing
from apps.deals import services as t
from apps.deals.models import Deal
from apps.platforms.models import Platform
from apps.registration.models import LegalEntityApplication
from apps.users.models import User

D = Decimal
_n = 0


def _advertiser(email="adv@test.com", **extra):
    user = User.objects.create_user(email=email, password="pass1234", role=User.Role.ADVERTISER,
                                    status=User.Status.ACTIVE, **extra)
    LegalEntityApplication.objects.create(user=user, company_name="ООО Ромашка", inn="123456789",
                                          status=LegalEntityApplication.Status.APPROVED)
    return user


def _draft(advertiser, budget="1500000", **extra):
    today = timezone.localdate()
    return Campaign.objects.create(
        **CARD_FIELDS, advertiser=advertiser, name="Кампания", payment_type=Campaign.PaymentType.FIXED,
        fixed_price=D("150000"), budget=D(budget), status=extra.pop("status", Campaign.Status.DRAFT),
        start_date=today, end_date=today + timedelta(days=30), deadline=today + timedelta(days=20), **extra,
    )


class TariffTableTest(TestCase):
    def test_tariffs_from_business_table(self):
        rows = list(Tariff.objects.values_list("code", "commission_percent", "campaigns_per_month", "min_campaign_budget"))
        self.assertEqual(rows, [
            ("free", D("10"), 1, D("1000000")), ("smm", D("10"), 10, D("3000000")),
            ("corp", D("7"), 30, D("3000000")), ("premium", D("5"), None, D("3000000")),
        ])
        self.assertEqual(Tariff.objects.get(is_default=True).code, "free")

    def test_contractor_fee_default_is_ten_percent(self):
        self.assertEqual(settings.CONTRACTOR_FEE_PERCENT, "10")


class CommissionRateTest(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.adv = _advertiser()
        Wallet.objects.filter(user=self.adv).update(available_balance=D("1000000"))
        self.blogger = User.objects.create_user(email="bl@test.com", password="pass1234", role=User.Role.BLOGGER,
                                                status=User.Status.ACTIVE)
        self.campaign = Campaign.objects.create(
            advertiser=self.adv, name="Кампания", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=D("100000"), budget=D("5000000"), status=Campaign.Status.ACTIVE,
            start_date=self.today, end_date=self.today + timedelta(days=40), approval_required=False,
            min_retention_days=1, **CARD_FIELDS,
        )

    def _response(self):
        global _n
        _n += 1
        platform = Platform.objects.create(
            blogger=self.blogger, social_type=Platform.SocialType.INSTAGRAM, url=f"https://instagram.com/tt{_n}",
            subscribers=12000, price_post=D("50000"), status=Platform.Status.APPROVED,
        )
        return CampaignResponse.objects.create(campaign=self.campaign, blogger=self.blogger, platform=platform,
                                               content_type="post", proposed_price=D("100000"))

    def test_new_advertiser_free_tariff(self):
        self.assertEqual(tariff_for(self.adv).code, "free")
        self.assertEqual(commission_percent_for(self.adv), D("10"))

    def test_assigned_tariff(self):
        assign_tariff(self.adv, Tariff.objects.get(code="premium"), None)
        self.assertEqual(commission_percent_for(self.adv), D("5"))

    def test_special_tariff_over_tariff(self):
        SpecialTariff.objects.create(advertiser=self.adv, percent=D("3"), note="Договор", approved_by="Директор")
        self.assertEqual(commission_percent_for(self.adv), D("3"))
        SpecialTariff.objects.filter(advertiser=self.adv).update(valid_until=self.today - timedelta(days=1))
        self.assertEqual(commission_percent_for(self.adv), D("10"))

    def test_offer_fixes_commission_and_reserves_price_plus_commission_on_accept(self):
        offer = accept_response(self._response().pk, self.adv, publication_day(self.campaign))
        self.assertEqual((offer.commission_percent, offer.reserved_commission), (D("10"), D("10000")))
        accept_direct_offer(offer.pk, self.blogger)
        wallet = Wallet.objects.get(user=self.adv)
        self.assertEqual((wallet.available_balance, wallet.reserved_balance), (D("890000"), D("110000")))

    @override_settings(CONTRACTOR_FEE_PERCENT="10")
    def test_rate_fixed_at_offer_two_fees_on_payout(self):
        resp = self._response()
        accept_response(resp.pk, self.adv, publication_day(self.campaign))
        assign_tariff(self.adv, Tariff.objects.get(code="premium"), None)  # позже — на оферту не влияет
        deal = blogger_accepts(resp)
        self.assertEqual((deal.commission_percent, deal.commission_amount), (D("10"), D("10000")))
        Deal.objects.filter(pk=deal.pk).update(publication_date=self.today)
        t.submit_publication(deal.pk, self.blogger, "https://instagram.com/p/1")
        Deal.objects.filter(pk=deal.pk).update(publication_at=timezone.now() - timedelta(days=10))
        t.auto_complete_overdue()
        self.assertEqual(Wallet.objects.get(user=self.blogger).on_withdrawal, D("90000"))  # цена − 10% платы
        self.assertEqual(Transaction.objects.get(deal=deal, type=Transaction.Type.PAYMENT).amount, D("-110000"))
        self.assertEqual(metrics.platform_revenue(), D("20000"))  # комиссия 10 000 + плата 10 000

    def test_old_deal_keeps_commission_from_amount(self):
        deal = Deal.objects.create(
            campaign=self.campaign, blogger=self.blogger, advertiser=self.adv, amount=D("100000"),
            platform=Platform.objects.create(blogger=self.blogger, social_type=Platform.SocialType.TELEGRAM,
                                             url="https://t.me/old", subscribers=1, status=Platform.Status.APPROVED),
            status=Deal.Status.CHECKING,
        )
        BillingService.reserve_funds(deal)
        BillingService.complete_deal_payment(deal)
        self.assertEqual(Wallet.objects.get(user=self.blogger).on_withdrawal, D("85000"))
        self.assertEqual(metrics.platform_revenue(), D("15000"))

    def test_insufficient_balance_message_includes_commission(self):
        from apps.campaigns.services import AcceptError

        Wallet.objects.filter(user=self.adv).update(available_balance=D("105000"))
        with self.assertRaisesMessage(AcceptError, "комиссия платформы 10%"):
            accept_response(self._response().pk, self.adv, publication_day(self.campaign))
        self.assertFalse(DirectOffer.objects.exists())

    def test_owner_sees_rate_and_tariff(self):
        self.client.force_login(self.adv)
        self._response()
        page = self.client.get(reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk})).content.decode()
        self.assertIn("комиссия платформы 10% — тариф «Бесплатно»", page)

    def test_contractor_fee_individual_rate(self):
        from apps.billing.models import ContractorFeeRate

        ContractorFeeRate.objects.create(blogger=self.blogger, percent=D("7"), approved_by="Директор")
        self.assertEqual(contractor_fee_percent_for(self.blogger), D("7"))


class TariffModerationTest(TestCase):
    def setUp(self):
        self.adv = _advertiser()
        self.client.force_login(self.adv)

    def _submit(self, campaign):
        self.client.post(reverse("web:campaign_submit", kwargs={"pk": campaign.pk}))
        campaign.refresh_from_db()
        return campaign

    def test_min_budget_free_tariff(self):
        campaign = _draft(self.adv, budget="900000")
        self.assertIn("Минимальный бюджет кампании на тарифе «Бесплатно» — 1 000 000", moderation_error(campaign))
        self.assertIn("бюджет не меньше 1 000 000 (тариф «Бесплатно»)", moderation_missing(campaign))
        self.assertEqual(self._submit(campaign).status, Campaign.Status.DRAFT)

    def test_min_budget_paid_tariff(self):
        assign_tariff(self.adv, Tariff.objects.get(code="smm"), None)
        self.assertIn("3 000 000", moderation_error(_draft(self.adv, budget="2000000")))

    def test_min_budget_skipped_for_campaign_approved_before(self):
        campaign = _draft(self.adv, budget="500000", status=Campaign.Status.REJECTED, approved_snapshot={"budget": "500000"})
        self.assertIsNone(moderation_error(campaign))

    def test_one_campaign_a_month_on_free(self):
        first = self._submit(_draft(self.adv))
        self.assertEqual(first.status, Campaign.Status.MODERATION)
        self.assertIsNotNone(first.submitted_at)
        second = _draft(self.adv)
        self.assertIn("лимит этого месяца исчерпан", moderation_error(second))
        self.assertEqual(self._submit(second).status, Campaign.Status.DRAFT)

    def test_rejected_campaign_resubmits_without_spending_limit(self):
        first = self._submit(_draft(self.adv))
        Campaign.objects.filter(pk=first.pk).update(status=Campaign.Status.REJECTED)
        first.refresh_from_db()
        submitted = first.submitted_at
        self.assertEqual(self._submit(first).status, Campaign.Status.MODERATION)
        self.assertEqual(first.submitted_at, submitted)  # дата первой отправки не меняется

    def test_rejected_campaign_does_not_count(self):
        first = self._submit(_draft(self.adv))
        Campaign.objects.filter(pk=first.pk).update(status=Campaign.Status.REJECTED)
        self.assertEqual(self._submit(_draft(self.adv)).status, Campaign.Status.MODERATION)

    def test_previous_month_does_not_count(self):
        first = self._submit(_draft(self.adv))
        Campaign.objects.filter(pk=first.pk).update(submitted_at=timezone.now() - timedelta(days=40))
        self.assertIsNone(moderation_error(_draft(self.adv)))

    def test_premium_unlimited_and_demo_not_limited(self):
        assign_tariff(self.adv, Tariff.objects.get(code="premium"), None)
        for _ in range(3):
            self.assertEqual(self._submit(_draft(self.adv, budget="3000000")).status, Campaign.Status.MODERATION)
        demo = _advertiser(email="demo-adv@test.com", is_demo=True)
        _draft(demo, status=Campaign.Status.MODERATION, submitted_at=timezone.now())
        self.assertIsNone(moderation_error(_draft(demo)))

    def test_api_submit_checks_limit(self):
        from rest_framework.test import APIClient

        self._submit(_draft(self.adv))
        api = APIClient()
        api.force_authenticate(self.adv)
        second = _draft(self.adv)
        response = api.post(f"/api/v1/campaigns/{second.pk}/submit_for_moderation/")
        self.assertEqual(response.status_code, 400)
        self.assertIn("лимит", response.json()["detail"])


class TariffPanelTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(email="staff@test.com", password="pass1234", is_staff=True,
                                              status=User.Status.ACTIVE)
        self.adv = _advertiser()
        self.client.force_login(self.staff)

    def test_staff_assigns_tariff(self):
        page = self.client.get(reverse("web:admin_users")).content.decode()
        self.assertIn("data-tariff-form", page)
        corp = Tariff.objects.get(code="corp")
        self.client.post(reverse("web:admin_user_tariff", kwargs={"pk": self.adv.pk}), {"tariff": corp.pk})
        assignment = TariffAssignment.objects.get(advertiser=self.adv)
        self.assertEqual((assignment.tariff, assignment.assigned_by), (corp, self.staff))
        self.assertEqual(commission_percent_for(self.adv), D("7"))

    def test_only_advertisers_and_staff(self):
        blogger = User.objects.create_user(email="b@test.com", password="pass1234", role=User.Role.BLOGGER)
        free = Tariff.objects.get(code="free")
        response = self.client.post(reverse("web:admin_user_tariff", kwargs={"pk": blogger.pk}), {"tariff": free.pk})
        self.assertEqual(response.status_code, 404)
        self.client.force_login(self.adv)
        self.client.post(reverse("web:admin_user_tariff", kwargs={"pk": self.adv.pk}), {"tariff": free.pk})
        self.assertFalse(TariffAssignment.objects.exists())
