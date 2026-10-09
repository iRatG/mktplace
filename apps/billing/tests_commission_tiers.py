"""
#38: комиссия платформы по тарифным уровням 16 / 13 / 8% от оборота рекламодателя за прошлый календарный месяц,
сверх вознаграждения исполнителя; специальный тариф; ставка фиксируется при направлении оферты; исполнитель получает
вознаграждение целиком; сделки до правила — прежние 15% из суммы.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.billing import metrics
from apps.billing.models import SpecialTariff, Transaction, Wallet
from apps.billing.services import BillingService
from apps.billing.tariffs import commission_percent_for, monthly_turnover, tier_percent
from apps.campaigns.models import Campaign, DirectOffer
from apps.campaigns.models import Response as CampaignResponse
from apps.campaigns.services import accept_response, reject_offer
from apps.campaigns.testing import CARD_FIELDS, blogger_accepts, publication_day
from apps.deals import services as t
from apps.deals.models import Deal
from apps.platforms.models import Platform
from apps.users.models import User

_n = 0


class TierTest(TestCase):
    def test_tier_boundaries(self):
        self.assertEqual(tier_percent(Decimal("0")), Decimal("16"))
        self.assertEqual(tier_percent(Decimal("120000000")), Decimal("16"))
        self.assertEqual(tier_percent(Decimal("120000000.01")), Decimal("13"))
        self.assertEqual(tier_percent(Decimal("1200000000")), Decimal("13"))
        self.assertEqual(tier_percent(Decimal("1200000001")), Decimal("8"))


class CommissionTest(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.adv = User.objects.create_user(email="adv@test.com", password="pass1234", role=User.Role.ADVERTISER,
                                            status=User.Status.ACTIVE)
        Wallet.objects.filter(user=self.adv).update(available_balance=Decimal("1000000"))
        self.blogger = User.objects.create_user(email="bl@test.com", password="pass1234", role=User.Role.BLOGGER,
                                                status=User.Status.ACTIVE)
        self.campaign = Campaign.objects.create(
            advertiser=self.adv, name="Кампания", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("100000"), budget=Decimal("5000000"), status=Campaign.Status.ACTIVE,
            start_date=self.today, end_date=self.today + timedelta(days=40), approval_required=False,
            min_retention_days=1, **CARD_FIELDS,
        )

    def _response(self):
        global _n
        _n += 1
        platform = Platform.objects.create(
            blogger=self.blogger, social_type=Platform.SocialType.INSTAGRAM, url=f"https://instagram.com/ct{_n}",
            subscribers=12000, price_post=Decimal("50000"), status=Platform.Status.APPROVED,
        )
        return CampaignResponse.objects.create(campaign=self.campaign, blogger=self.blogger, platform=platform,
                                               content_type="post", proposed_price=Decimal("100000"))

    def _completed_deal_in(self, moment, amount):
        """Оплаченная сделка с заданным моментом оплаты — для оборота."""
        return Deal.objects.create(
            campaign=self.campaign, blogger=self.blogger, advertiser=self.adv,
            platform=Platform.objects.create(blogger=self.blogger, social_type=Platform.SocialType.TELEGRAM,
                                             url=f"https://t.me/x{moment.timestamp()}", subscribers=1,
                                             status=Platform.Status.APPROVED),
            amount=Decimal(amount), status=Deal.Status.COMPLETED, last_distributed_at=moment,
        )

    # ── Ставка ───────────────────────────────────────────────────────────────

    def test_new_advertiser_first_tier(self):
        self.assertEqual(commission_percent_for(self.adv), Decimal("16"))

    def test_rate_by_previous_month_turnover(self):
        first = self.today.replace(day=1)
        prev = timezone.make_aware(datetime.combine(first - timedelta(days=3), time(12)))
        this_month = timezone.now()
        self._completed_deal_in(prev, "130000000")
        self._completed_deal_in(this_month, "2000000000")  # текущий месяц на ставку этого месяца не влияет
        self.assertEqual(monthly_turnover(self.adv, prev.year, prev.month), Decimal("130000000"))
        self.assertEqual(commission_percent_for(self.adv), Decimal("13"))

    def test_special_tariff(self):
        SpecialTariff.objects.create(advertiser=self.adv, percent=Decimal("5"), note="Договор")
        self.assertEqual(commission_percent_for(self.adv), Decimal("5"))
        SpecialTariff.objects.filter(advertiser=self.adv).update(valid_until=self.today - timedelta(days=1))
        self.assertEqual(commission_percent_for(self.adv), Decimal("16"))

    # ── Резерв, выплата, возврат ─────────────────────────────────────────────

    def test_offer_reserves_price_plus_commission_and_releases_all(self):
        offer = accept_response(self._response().pk, self.adv, publication_day(self.campaign))
        self.assertEqual((offer.commission_percent, offer.reserved_commission), (Decimal("16"), Decimal("16000")))
        wallet = Wallet.objects.get(user=self.adv)
        self.assertEqual((wallet.available_balance, wallet.reserved_balance), (Decimal("884000"), Decimal("116000")))
        reject_offer(offer.pk, self.blogger)
        wallet.refresh_from_db()
        self.assertEqual((wallet.available_balance, wallet.reserved_balance), (Decimal("1000000"), Decimal("0")))

    def test_rate_fixed_at_offer_and_blogger_gets_full_price(self):
        resp = self._response()
        accept_response(resp.pk, self.adv, publication_day(self.campaign))
        SpecialTariff.objects.create(advertiser=self.adv, percent=Decimal("5"))  # позже — не влияет на оферту
        deal = blogger_accepts(resp)
        self.assertEqual((deal.commission_percent, deal.commission_amount), (Decimal("16"), Decimal("16000")))
        Deal.objects.filter(pk=deal.pk).update(publication_date=self.today)
        t.submit_publication(deal.pk, self.blogger, "https://instagram.com/p/1")
        Deal.objects.filter(pk=deal.pk).update(publication_at=timezone.now() - timedelta(days=10))
        t.auto_complete_overdue()
        self.assertEqual(Wallet.objects.get(user=self.blogger).available_balance, Decimal("100000"))
        adv = Wallet.objects.get(user=self.adv)
        self.assertEqual((adv.available_balance, adv.reserved_balance), (Decimal("884000"), Decimal("0")))
        self.assertEqual(Transaction.objects.get(deal=deal, type=Transaction.Type.PAYMENT).amount, Decimal("-116000"))
        self.assertEqual(metrics.platform_revenue(), Decimal("16000"))

    def test_old_deal_keeps_commission_from_amount(self):
        deal = Deal.objects.create(
            campaign=self.campaign, blogger=self.blogger, advertiser=self.adv, amount=Decimal("100000"),
            platform=Platform.objects.create(blogger=self.blogger, social_type=Platform.SocialType.TELEGRAM,
                                             url="https://t.me/old", subscribers=1, status=Platform.Status.APPROVED),
            status=Deal.Status.CHECKING,
        )
        BillingService.reserve_funds(deal)
        BillingService.complete_deal_payment(deal)
        self.assertEqual(Wallet.objects.get(user=self.blogger).available_balance, Decimal("85000"))
        self.assertEqual(metrics.platform_revenue(), Decimal("15000"))

    def test_insufficient_balance_message_includes_commission(self):
        from apps.campaigns.services import AcceptError

        Wallet.objects.filter(user=self.adv).update(available_balance=Decimal("110000"))
        with self.assertRaisesMessage(AcceptError, "комиссия платформы 16%"):
            accept_response(self._response().pk, self.adv, publication_day(self.campaign))
        self.assertFalse(DirectOffer.objects.exists())

    def test_owner_sees_rate(self):
        self.client.force_login(self.adv)
        self._response()
        page = self.client.get(reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk})).content.decode()
        self.assertIn("комиссия платформы 16%", page)
