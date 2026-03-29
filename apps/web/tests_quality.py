"""
Sprint 9 — Quality tests.
Covers: Celery fallback, CPA rate limiting, campaign validation, pagination, 404/500.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import Wallet
from apps.campaigns.models import Campaign
from apps.deals.models import Deal, DealStatusLog
from apps.platforms.models import Platform

User = get_user_model()


def _make_user(email, role, password="pass1234"):
    u = User.objects.create_user(email=email, password=password, role=role)
    u.is_active = True
    u.save()
    return u


def _make_campaign(advertiser, payment_type=Campaign.PaymentType.FIXED,
                   budget=1_000_000, fixed_price=500_000):
    return Campaign.objects.create(
        advertiser=advertiser,
        name="Test Campaign",
        payment_type=payment_type,
        budget=budget,
        fixed_price=fixed_price,
        status=Campaign.Status.ACTIVE,
        deadline=timezone.now().date() + timedelta(days=30),
    )


def _make_platform(blogger):
    return Platform.objects.create(
        blogger=blogger,
        social_type=Platform.SocialType.TELEGRAM,
        url="https://t.me/test",
        subscribers=10000,
        status=Platform.Status.APPROVED,
    )


def _make_deal(advertiser, blogger, campaign=None, platform=None,
               amount=500_000, status=Deal.Status.IN_PROGRESS):
    if campaign is None:
        campaign = _make_campaign(advertiser)
    if platform is None:
        platform = _make_platform(blogger)
    return Deal.objects.create(
        advertiser=advertiser,
        blogger=blogger,
        campaign=campaign,
        platform=platform,
        amount=Decimal(amount),
        status=status,
    )


def _fund_wallet(user, amount):
    wallet, _ = Wallet.objects.get_or_create(user=user)
    wallet.available_balance = Decimal(amount)
    wallet.save()
    return wallet


# ── Block A: Celery fallback ───────────────────────────────────────────────────

class DealDetailFallbackTest(TestCase):
    """A2: deal_detail auto-completes CHECKING deals older than 72h."""

    def setUp(self):
        self.adv = _make_user("adv_fb@test.com", User.Role.ADVERTISER)
        self.blg = _make_user("blg_fb@test.com", User.Role.BLOGGER)
        _fund_wallet(self.adv, 2_000_000)
        _fund_wallet(self.blg, 0)
        self.deal = _make_deal(self.adv, self.blg, status=Deal.Status.CHECKING)
        # Fund escrow (simulate frozen balance)
        adv_wallet = Wallet.objects.get(user=self.adv)
        adv_wallet.frozen_balance = Decimal(500_000)
        adv_wallet.available_balance = Decimal(1_500_000)
        adv_wallet.save()

    def test_deal_auto_completes_on_detail_open_after_72h(self):
        """Opening deal detail page auto-completes an overdue CHECKING deal."""
        # Backdate updated_at by 73 hours
        Deal.objects.filter(pk=self.deal.pk).update(
            updated_at=timezone.now() - timedelta(hours=73)
        )
        self.client.force_login(self.adv)
        url = reverse("web:deal_detail", args=[self.deal.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.COMPLETED)

    def test_deal_not_autocompleted_if_under_72h(self):
        """Deal stays CHECKING if updated_at is only 71h ago."""
        Deal.objects.filter(pk=self.deal.pk).update(
            updated_at=timezone.now() - timedelta(hours=71)
        )
        self.client.force_login(self.adv)
        url = reverse("web:deal_detail", args=[self.deal.pk])
        self.client.get(url)
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.CHECKING)


# ── Block B: CPA Rate limiting ────────────────────────────────────────────────

class CPARateLimitTest(TestCase):
    """B1: rate limiting on /t/<slug>/ endpoint."""

    def setUp(self):
        self.adv = _make_user("adv_rl@test.com", User.Role.ADVERTISER)
        self.blg = _make_user("blg_rl@test.com", User.Role.BLOGGER)
        _fund_wallet(self.adv, 5_000_000)
        campaign = Campaign.objects.create(
            advertiser=self.adv,
            name="CPA Campaign",
            payment_type=Campaign.PaymentType.CPA,
            budget=Decimal("5000000"),
            cpa_type=Campaign.CPAType.CLICK,
            cpa_rate=Decimal("1000"),
            cpa_tracking_url="https://example.com",
            status=Campaign.Status.ACTIVE,
            deadline=timezone.now().date() + timedelta(days=30),
        )
        platform = _make_platform(self.blg)
        self.deal = _make_deal(self.adv, self.blg, campaign=campaign,
                               platform=platform, status=Deal.Status.IN_PROGRESS)
        from apps.deals.models import TrackingLink
        self.tl = TrackingLink.objects.create(deal=self.deal)
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_31st_click_from_same_ip_is_silently_skipped(self):
        """After 30 clicks, 31st click is not logged (rate limited)."""
        from apps.deals.models import ClickLog
        url = reverse("web:cpa_click_track", args=[self.tl.slug])
        for _ in range(30):
            self.client.get(url, REMOTE_ADDR="1.2.3.4")
        count_before = ClickLog.objects.filter(tracking_link=self.tl).count()
        self.client.get(url, REMOTE_ADDR="1.2.3.4")
        count_after = ClickLog.objects.filter(tracking_link=self.tl).count()
        self.assertEqual(count_before, count_after,
                         "31st click should be silently skipped (rate limited)")

    def test_different_ips_not_rate_limited_together(self):
        """Clicks from different IPs are tracked independently."""
        from apps.deals.models import ClickLog
        url = reverse("web:cpa_click_track", args=[self.tl.slug])
        self.client.get(url, REMOTE_ADDR="1.1.1.1")
        self.client.get(url, REMOTE_ADDR="2.2.2.2")
        self.assertEqual(ClickLog.objects.filter(tracking_link=self.tl).count(), 2)


class PostbackRateLimitTest(TestCase):
    """B1: rate limiting on /pb/ endpoint."""

    def test_101st_postback_from_same_ip_returns_429(self):
        """After 100 postbacks, 101st returns HTTP 429."""
        cache.clear()
        url = reverse("web:cpa_postback")
        for _ in range(100):
            self.client.get(url + "?click_id=invalid", REMOTE_ADDR="9.9.9.9")
        response = self.client.get(url + "?click_id=invalid", REMOTE_ADDR="9.9.9.9")
        self.assertEqual(response.status_code, 429)
        cache.clear()


# ── Block B2: Campaign form validation ────────────────────────────────────────

class CampaignFormValidationTest(TestCase):
    """B2: CampaignForm validates deadline, budget, CPA fields."""

    def setUp(self):
        self.adv = _make_user("adv_val@test.com", User.Role.ADVERTISER)
        self.client.force_login(self.adv)
        # Fund wallet
        _fund_wallet(self.adv, 5_000_000)

    def _post_campaign(self, data):
        url = reverse("web:campaign_create")
        return self.client.post(url, data)

    def test_deadline_in_past_is_rejected(self):
        yesterday = (timezone.now().date() - timedelta(days=1)).isoformat()
        resp = self._post_campaign({
            "name": "Test", "payment_type": "fixed", "fixed_price": "500000",
            "budget": "1000000", "deadline": yesterday,
        })
        self.assertEqual(resp.status_code, 200)
        form = resp.context.get("form")
        self.assertIsNotNone(form)
        self.assertIn("deadline", form.errors, "deadline error expected")

    def test_cpa_without_cpa_type_is_rejected(self):
        future = (timezone.now().date() + timedelta(days=30)).isoformat()
        resp = self._post_campaign({
            "name": "CPA Test", "payment_type": "cpa",
            "budget": "1000000", "deadline": future,
            "cpa_rate": "500",
        })
        self.assertEqual(resp.status_code, 200)
        form = resp.context.get("form")
        self.assertIsNotNone(form)
        self.assertIn("cpa_type", form.errors, "cpa_type error expected")

    def test_cpa_with_zero_rate_is_rejected(self):
        future = (timezone.now().date() + timedelta(days=30)).isoformat()
        resp = self._post_campaign({
            "name": "CPA Test", "payment_type": "cpa",
            "budget": "1000000", "deadline": future,
            "cpa_type": "lead", "cpa_rate": "0",
        })
        self.assertEqual(resp.status_code, 200)
        form = resp.context.get("form")
        self.assertIsNotNone(form)
        self.assertIn("cpa_rate", form.errors, "cpa_rate error expected")

    def test_zero_budget_is_rejected(self):
        future = (timezone.now().date() + timedelta(days=30)).isoformat()
        resp = self._post_campaign({
            "name": "Test", "payment_type": "fixed",
            "fixed_price": "500000", "budget": "0",
            "deadline": future,
        })
        self.assertEqual(resp.status_code, 200)
        form = resp.context.get("form")
        self.assertIsNotNone(form)
        self.assertIn("budget", form.errors, "budget error expected")


# ── Block C: Pagination ────────────────────────────────────────────────────────

class PaginationTest(TestCase):
    """C: Lists are paginated at 20 items per page."""

    def setUp(self):
        self.adv = _make_user("adv_pg@test.com", User.Role.ADVERTISER)
        _fund_wallet(self.adv, 50_000_000)
        self.client.force_login(self.adv)

    def test_campaign_list_paginated_at_20(self):
        for i in range(25):
            Campaign.objects.create(
                advertiser=self.adv,
                name=f"Campaign {i}",
                payment_type=Campaign.PaymentType.FIXED,
                fixed_price=Decimal("100000"),
                budget=Decimal("500000"),
                status=Campaign.Status.ACTIVE,
                deadline=timezone.now().date() + timedelta(days=30),
            )
        url = reverse("web:campaign_list")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        # page 1: 20 items
        self.assertEqual(len(resp.context["campaigns"].object_list), 20)
        resp2 = self.client.get(url + "?page=2")
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(len(resp2.context["campaigns"].object_list), 5)

    def test_deal_list_paginated_at_20(self):
        blg = _make_user("blg_pg@test.com", User.Role.BLOGGER)
        campaign = _make_campaign(self.adv)
        platform = _make_platform(blg)
        for _ in range(25):
            Deal.objects.create(
                advertiser=self.adv, blogger=blg,
                campaign=campaign, platform=platform,
                amount=Decimal("100000"), status=Deal.Status.IN_PROGRESS,
            )
        url = reverse("web:deal_list")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["deals"].object_list), 20)


# ── Block D4: Custom 404 / 500 ────────────────────────────────────────────────

class ErrorPagesTest(TestCase):
    """D4: Custom 404 and 500 pages render correctly."""

    @override_settings(DEBUG=False)
    def test_404_view_renders(self):
        """Custom page_not_found_view returns 404 with our template."""
        from config.urls import page_not_found_view
        from django.test import RequestFactory
        rf = RequestFactory()
        request = rf.get("/nonexistent/")
        response = page_not_found_view(request)
        self.assertEqual(response.status_code, 404)
        self.assertIn(b"404", response.content)

    @override_settings(DEBUG=False)
    def test_500_view_renders(self):
        from config.urls import server_error_view
        from django.test import RequestFactory
        rf = RequestFactory()
        request = rf.get("/")
        response = server_error_view(request)
        self.assertEqual(response.status_code, 500)
        self.assertIn(b"500", response.content)


# ── Block E: Views package smoke test ─────────────────────────────────────────

class ViewsPackageImportTest(TestCase):
    """E: All views importable from the new views package."""

    def test_all_views_importable(self):
        from apps.web import views
        # Spot check key functions across all modules
        for fn_name in [
            "login_view", "register_view", "logout_view",
            "landing", "faq",
            "campaign_list", "campaign_detail", "campaign_create",
            "deal_list", "deal_detail", "deal_submit_publication",
            "wallet_view",
            "blogger_catalog", "direct_offer_create",
            "admin_dashboard", "admin_campaigns",
            "notification_list",
            "analytics_view",
            "cpa_click_track", "cpa_postback",
        ]:
            self.assertTrue(
                hasattr(views, fn_name),
                f"views.{fn_name} not found in views package"
            )

    def test_urls_resolve_correctly(self):
        """All key URLs resolve without import errors."""
        from django.urls import reverse as r
        urls_to_check = [
            ("web:landing", []),
            ("web:faq", []),
            ("web:campaign_list", []),
            ("web:deal_list", []),
            ("web:wallet", []),
            ("web:notifications", []),
            ("web:blogger_catalog", []),
        ]
        for name, args in urls_to_check:
            try:
                r(name, args=args)
            except Exception as e:
                self.fail(f"URL '{name}' failed to resolve: {e}")
