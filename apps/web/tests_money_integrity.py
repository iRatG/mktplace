"""
#19: каждое движение денег — через BillingService и с транзакцией; Django admin не обходит процесс;
CPA списывается только по сделке в работе и в пределах бюджета кампании.
"""
from decimal import Decimal

from django.contrib.admin.sites import site
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.billing.models import Transaction, Wallet, WithdrawalRequest
from apps.billing.services import BillingService
from apps.campaigns.models import Campaign
from apps.deals.models import Conversion, Deal, TrackingLink
from apps.platforms.models import Platform
from apps.users.models import User

D = Decimal


def _user(email, role=User.Role.BLOGGER, **extra):
    return User.objects.create_user(email=email, password="pass1234", role=role, status=User.Status.ACTIVE, **extra)


def _wallet(user, **balances):
    Wallet.objects.filter(user=user).update(**{k: D(v) for k, v in balances.items()})
    return Wallet.objects.get(user=user)


class WithdrawalTest(TestCase):
    def setUp(self):
        self.staff = _user("staff@test.com", User.Role.ADVERTISER, is_staff=True, is_superuser=True)
        self.blogger = _user("bl@test.com")
        _wallet(self.blogger, available_balance="500000")
        self.wr = WithdrawalRequest.objects.create(
            blogger=self.blogger, amount=D("100000"), requisites={"type": "card", "details": "8600"},
        )
        BillingService.process_withdrawal(self.wr)

    def test_panel_payout_writes_transaction(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("web:admin_withdrawal_approve", kwargs={"pk": self.wr.pk}))
        self.wr.refresh_from_db()
        wallet = Wallet.objects.get(user=self.blogger)
        self.assertEqual(self.wr.status, WithdrawalRequest.Status.COMPLETED)
        self.assertEqual(wallet.on_withdrawal, D("0"))
        payout = Transaction.objects.get(wallet=wallet, type=Transaction.Type.PAYOUT)
        self.assertEqual(payout.amount, D("-100000"))

    def test_panel_reject_returns_money(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("web:admin_withdrawal_reject", kwargs={"pk": self.wr.pk}), {"comment": "нет"})
        wallet = Wallet.objects.get(user=self.blogger)
        self.assertEqual((wallet.available_balance, wallet.on_withdrawal), (D("500000"), D("0")))

    def test_payout_twice_refused(self):
        BillingService.complete_withdrawal(self.wr)
        with self.assertRaises(ValueError):
            BillingService.complete_withdrawal(self.wr)

    def test_payout_with_broken_balance_refused(self):
        _wallet(self.blogger, on_withdrawal="0")
        with self.assertRaises(ValueError):
            BillingService.complete_withdrawal(self.wr)

    def test_django_admin_actions_use_service(self):
        admin = site._registry[WithdrawalRequest]
        request = RequestFactory().post("/")
        request.user = self.staff
        request._messages = type("M", (), {"add": lambda *a, **k: None})()
        admin.complete_withdrawals(request, WithdrawalRequest.objects.all())
        self.assertTrue(Transaction.objects.filter(type=Transaction.Type.PAYOUT).exists())
        self.assertEqual(Wallet.objects.get(user=self.blogger).on_withdrawal, D("0"))

    def test_demo_blogger_cannot_withdraw_on_site(self):
        demo = _user("demo@test.com", is_demo=True)
        _wallet(demo, available_balance="500000")
        self.client.force_login(demo)
        self.client.post(reverse("web:wallet"), {"amount": "100000", "card": "8600 0000 0000 0000"})
        self.assertFalse(WithdrawalRequest.objects.filter(blogger=demo).exists())

    def test_api_withdrawal_atomic(self):
        poor = _user("poor@test.com")
        api = APIClient()
        api.force_authenticate(poor)
        r = api.post(reverse("billing:withdrawal-list"),
                     {"amount": "100000", "requisites": {"type": "card", "details": "x"}}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(WithdrawalRequest.objects.filter(blogger=poor).exists())


class StrictReserveTest(TestCase):
    def test_release_without_reserve_refused(self):
        adv = _user("adv@test.com", User.Role.ADVERTISER)
        blogger = _user("b@test.com")
        campaign = Campaign.objects.create(advertiser=adv, name="К", budget=D("500000"), fixed_price=D("100000"))
        platform = Platform.objects.create(blogger=blogger, social_type=Platform.SocialType.INSTAGRAM,
                                           url="https://instagram.com/b", subscribers=1)
        deal = Deal.objects.create(campaign=campaign, blogger=blogger, advertiser=adv, platform=platform,
                                   amount=D("100000"), status=Deal.Status.IN_PROGRESS)
        with self.assertRaises(ValueError):
            BillingService.release_funds(deal)
        self.assertEqual(Wallet.objects.get(user=adv).available_balance, D("0"))


class AdminReadOnlyTest(TestCase):
    def test_money_and_status_fields_read_only(self):
        from apps.campaigns.models import Campaign as C

        self.assertIn("status", site._registry[Deal].readonly_fields)
        self.assertIn("amount", site._registry[Deal].readonly_fields)
        self.assertIn("available_balance", site._registry[Wallet].readonly_fields)
        self.assertIn("status", site._registry[WithdrawalRequest].readonly_fields)
        self.assertIn("status", site._registry[C].readonly_fields)
        self.assertFalse(site._registry[Transaction].has_change_permission(None))
        self.assertEqual(getattr(site._registry[C], "actions", None) or [], [])


class CpaBillingTest(TestCase):
    def setUp(self):
        self.adv = _user("adv@test.com", User.Role.ADVERTISER)
        self.blogger = _user("b@test.com")
        _wallet(self.adv, available_balance="1000000")
        self.campaign = Campaign.objects.create(
            advertiser=self.adv, name="CPA", budget=D("10000"), payment_type=Campaign.PaymentType.CPA,
            cpa_type=Campaign.CPAType.CLICK, cpa_rate=D("4000"), status=Campaign.Status.ACTIVE,
        )
        platform = Platform.objects.create(blogger=self.blogger, social_type=Platform.SocialType.INSTAGRAM,
                                           url="https://instagram.com/b", subscribers=1)
        self.deal = Deal.objects.create(campaign=self.campaign, blogger=self.blogger, advertiser=self.adv,
                                        platform=platform, amount=D("0"), status=Deal.Status.IN_PROGRESS)
        self.link = TrackingLink.objects.create(deal=self.deal)

    def _conversion(self):
        return Conversion.objects.create(tracking_link=self.link, amount=D("4000"))

    def test_conversions_stop_at_campaign_budget(self):
        BillingService.credit_cpa_conversion(self._conversion())
        BillingService.credit_cpa_conversion(self._conversion())
        with self.assertRaises(ValueError):
            BillingService.credit_cpa_conversion(self._conversion())  # 8 000 из 10 000 уже потрачено
        self.assertEqual(Conversion.objects.filter(credited=True).count(), 2)

    def test_no_billing_after_deal_completed(self):
        Deal.objects.filter(pk=self.deal.pk).update(status=Deal.Status.COMPLETED)
        with self.assertRaises(ValueError):
            BillingService.credit_cpa_conversion(Conversion.objects.create(tracking_link=self.link, amount=D("4000")))

    @override_settings(RATELIMIT_ENABLED=True)
    def test_click_limit_ignores_spoofed_forwarded_for(self):
        from django.core.cache import cache

        cache.clear()
        url = reverse("web:cpa_click_track", kwargs={"slug": self.link.slug})
        for i in range(31):
            self.client.get(url, HTTP_X_FORWARDED_FOR=f"10.0.0.{i}", HTTP_X_REAL_IP="203.0.113.5")
        # 30 кликов в час с одного настоящего IP — остальное не логируется, сколько бы X-Forwarded-For ни менять
        self.assertEqual(self.link.clicks.count(), 30)
