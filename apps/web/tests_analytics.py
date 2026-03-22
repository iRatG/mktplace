"""
Tests for Module 12: Analytics
- AnalyticsAccessTest: routing by role, auth guard, staff redirect
- AdvertiserAnalyticsTest: correct metrics, zero-state, completed deals list
- BloggerAnalyticsTest: correct metrics, rating, responses stats, zero-state
- AdminDashboardAnalyticsTest: platform revenue, new users, top lists
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.billing.models import Transaction, Wallet
from apps.campaigns.models import Campaign
from apps.campaigns.models import Response as CampaignResponse
from apps.deals.models import Deal
from apps.platforms.models import Category, Platform
from apps.profiles.models import BloggerProfile
from apps.users.models import User


def _make_user(email, role=None, is_staff=False):
    u = User.objects.create_user(
        email=email,
        password="Test1234!",
        role=role or User.Role.ADVERTISER,
        status=User.Status.ACTIVE,
        is_staff=is_staff,
    )
    return u


def _make_wallet(user, available=Decimal("0"), reserved=Decimal("0")):
    wallet, _ = Wallet.objects.get_or_create(user=user)
    wallet.available_balance = available
    wallet.reserved_balance = reserved
    wallet.save(update_fields=["available_balance", "reserved_balance"])
    return wallet


def _make_campaign(advertiser, status=Campaign.Status.ACTIVE):
    return Campaign.objects.create(
        advertiser=advertiser,
        name="Test Campaign",
        description="desc",
        budget=Decimal("500000"),
        status=status,
    )


def _make_platform(blogger, status=Platform.Status.APPROVED):
    return Platform.objects.create(
        blogger=blogger,
        social_type=Platform.SocialType.INSTAGRAM,
        url="https://instagram.com/test",
        status=status,
        subscribers=1000,
        price_post=Decimal("50000"),
    )


def _make_deal(campaign, advertiser, blogger, platform, status=Deal.Status.COMPLETED, amount=Decimal("100000")):
    return Deal.objects.create(
        campaign=campaign,
        blogger=blogger,
        advertiser=advertiser,
        platform=platform,
        amount=amount,
        status=status,
    )


class AnalyticsAccessTest(TestCase):
    """Проверяем доступ: анонимный, advertiser, blogger, staff."""

    def setUp(self):
        self.advertiser = _make_user("adv@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("blog@test.com", User.Role.BLOGGER)
        self.staff = _make_user("staff@test.com", is_staff=True)
        self.url = reverse("web:analytics")

    def test_anonymous_redirects_to_login(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp["Location"])

    def test_advertiser_gets_200(self):
        self.client.force_login(self.advertiser)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "analytics/advertiser.html")

    def test_blogger_gets_200(self):
        self.client.force_login(self.blogger)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "analytics/blogger.html")

    def test_staff_redirects_to_admin_dashboard(self):
        self.client.force_login(self.staff)
        resp = self.client.get(self.url)
        self.assertRedirects(resp, reverse("web:admin_dashboard"), fetch_redirect_response=False)


class AdvertiserAnalyticsTest(TestCase):
    """Аналитика рекламодателя: метрики сделок и финансов."""

    def setUp(self):
        self.advertiser = _make_user("adv@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("blog@test.com", User.Role.BLOGGER)
        self.wallet_adv = _make_wallet(self.advertiser, available=Decimal("1000000"))
        _make_wallet(self.blogger)
        self.platform = _make_platform(self.blogger)
        self.campaign = _make_campaign(self.advertiser)
        self.client.force_login(self.advertiser)
        self.url = reverse("web:analytics")

    def test_zero_state_no_deals(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["total_deals"], 0)
        self.assertEqual(resp.context["completed_deals"], 0)
        self.assertEqual(resp.context["completion_rate"], 0)

    def test_total_deals_count(self):
        _make_deal(self.campaign, self.advertiser, self.blogger, self.platform, Deal.Status.COMPLETED)
        _make_deal(self.campaign, self.advertiser, self.blogger, self.platform, Deal.Status.CANCELLED)
        resp = self.client.get(self.url)
        self.assertEqual(resp.context["total_deals"], 2)
        self.assertEqual(resp.context["completed_deals"], 1)
        self.assertEqual(resp.context["cancelled_deals"], 1)

    def test_completion_rate_calculated(self):
        for _ in range(3):
            _make_deal(self.campaign, self.advertiser, self.blogger, self.platform, Deal.Status.COMPLETED)
        _make_deal(self.campaign, self.advertiser, self.blogger, self.platform, Deal.Status.CANCELLED)
        resp = self.client.get(self.url)
        self.assertEqual(resp.context["completion_rate"], 75)

    def test_total_spent_from_transactions(self):
        Transaction.objects.create(
            wallet=self.wallet_adv,
            type=Transaction.Type.PAYMENT,
            amount=Decimal("150000"),
            balance_after=Decimal("850000"),
        )
        resp = self.client.get(self.url)
        self.assertEqual(resp.context["total_spent"], Decimal("150000"))

    def test_total_deposited_from_transactions(self):
        Transaction.objects.create(
            wallet=self.wallet_adv,
            type=Transaction.Type.DEPOSIT,
            amount=Decimal("500000"),
            balance_after=Decimal("500000"),
        )
        resp = self.client.get(self.url)
        self.assertEqual(resp.context["total_deposited"], Decimal("500000"))

    def test_campaigns_by_status_counts(self):
        Campaign.objects.create(
            advertiser=self.advertiser, name="Draft", description="d",
            budget=Decimal("100000"), status=Campaign.Status.DRAFT,
        )
        resp = self.client.get(self.url)
        # campaign from setUp is ACTIVE
        self.assertEqual(resp.context["campaigns_by_status"]["active"], 1)
        self.assertEqual(resp.context["campaigns_by_status"]["draft"], 1)

    def test_recent_completed_in_context(self):
        deal = _make_deal(self.campaign, self.advertiser, self.blogger, self.platform)
        resp = self.client.get(self.url)
        self.assertIn(deal, list(resp.context["recent_completed"]))

    def test_only_own_deals_counted(self):
        other_adv = _make_user("other@test.com", User.Role.ADVERTISER)
        other_campaign = _make_campaign(other_adv)
        _make_deal(other_campaign, other_adv, self.blogger, self.platform)
        resp = self.client.get(self.url)
        self.assertEqual(resp.context["total_deals"], 0)


class BloggerAnalyticsTest(TestCase):
    """Аналитика блогера: заработок, рейтинг, отклики."""

    def setUp(self):
        self.advertiser = _make_user("adv@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("blog@test.com", User.Role.BLOGGER)
        _make_wallet(self.advertiser, available=Decimal("1000000"))
        self.wallet_blog = _make_wallet(self.blogger)
        self.platform = _make_platform(self.blogger)
        self.campaign = _make_campaign(self.advertiser)
        self.client.force_login(self.blogger)
        self.url = reverse("web:analytics")

    def test_zero_state(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["total_deals"], 0)
        self.assertEqual(resp.context["total_responses"], 0)
        self.assertEqual(resp.context["acceptance_rate"], 0)

    def test_total_earned_from_transactions(self):
        Transaction.objects.create(
            wallet=self.wallet_blog,
            type=Transaction.Type.EARNING,
            amount=Decimal("85000"),
            balance_after=Decimal("85000"),
        )
        resp = self.client.get(self.url)
        self.assertEqual(resp.context["total_earned"], Decimal("85000"))

    def test_deals_completed_count(self):
        _make_deal(self.campaign, self.advertiser, self.blogger, self.platform, Deal.Status.COMPLETED)
        _make_deal(self.campaign, self.advertiser, self.blogger, self.platform, Deal.Status.IN_PROGRESS)
        resp = self.client.get(self.url)
        self.assertEqual(resp.context["total_deals"], 2)
        self.assertEqual(resp.context["completed_deals"], 1)
        self.assertEqual(resp.context["active_deals"], 1)

    def test_acceptance_rate(self):
        # Use separate campaigns per response to avoid unique(blogger, campaign, platform)
        for i in range(2):
            c = _make_campaign(self.advertiser)
            CampaignResponse.objects.create(
                campaign=c, blogger=self.blogger, platform=self.platform,
                content_type="post", proposed_price=Decimal("50000"),
                status=CampaignResponse.Status.ACCEPTED,
            )
        c2 = _make_campaign(self.advertiser)
        CampaignResponse.objects.create(
            campaign=c2, blogger=self.blogger, platform=self.platform,
            content_type="post", proposed_price=Decimal("50000"),
            status=CampaignResponse.Status.REJECTED,
        )
        resp = self.client.get(self.url)
        self.assertEqual(resp.context["total_responses"], 3)
        self.assertEqual(resp.context["accepted_responses"], 2)
        # 2/3 = 67%
        self.assertEqual(resp.context["acceptance_rate"], 67)

    def test_rating_in_context(self):
        profile = BloggerProfile.objects.get(user=self.blogger)
        profile.rating = Decimal("4.5")
        profile.save()
        resp = self.client.get(self.url)
        self.assertEqual(resp.context["rating"], Decimal("4.5"))

    def test_only_own_deals_counted(self):
        other_blogger = _make_user("other@test.com", User.Role.BLOGGER)
        other_platform = _make_platform(other_blogger)
        _make_deal(self.campaign, self.advertiser, other_blogger, other_platform)
        resp = self.client.get(self.url)
        self.assertEqual(resp.context["total_deals"], 0)


class AdminDashboardAnalyticsTest(TestCase):
    """Аналитика на дашборде администратора."""

    def setUp(self):
        self.staff = _make_user("staff@test.com", is_staff=True)
        self.advertiser = _make_user("adv@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("blog@test.com", User.Role.BLOGGER)
        self.wallet_adv = _make_wallet(self.advertiser)
        self.wallet_blog = _make_wallet(self.blogger)
        self.client.force_login(self.staff)
        self.url = reverse("web:admin_dashboard")

    def test_dashboard_200(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_platform_revenue_calculation(self):
        Transaction.objects.create(
            wallet=self.wallet_adv, type=Transaction.Type.PAYMENT,
            amount=Decimal("100000"), balance_after=Decimal("0"),
        )
        Transaction.objects.create(
            wallet=self.wallet_blog, type=Transaction.Type.EARNING,
            amount=Decimal("85000"), balance_after=Decimal("85000"),
        )
        resp = self.client.get(self.url)
        self.assertEqual(resp.context["platform_revenue"], Decimal("15000"))

    def test_platform_revenue_zero_no_transactions(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.context["platform_revenue"], Decimal("0"))

    def test_new_users_month_in_context(self):
        resp = self.client.get(self.url)
        # staff + advertiser + blogger all created in setUp
        self.assertGreaterEqual(resp.context["new_users_month"], 3)

    def test_top_advertisers_present(self):
        Transaction.objects.create(
            wallet=self.wallet_adv, type=Transaction.Type.PAYMENT,
            amount=Decimal("200000"), balance_after=Decimal("0"),
        )
        resp = self.client.get(self.url)
        emails = [row["wallet__user__email"] for row in resp.context["top_advertisers"]]
        self.assertIn("adv@test.com", emails)

    def test_top_bloggers_present(self):
        Transaction.objects.create(
            wallet=self.wallet_blog, type=Transaction.Type.EARNING,
            amount=Decimal("170000"), balance_after=Decimal("170000"),
        )
        resp = self.client.get(self.url)
        emails = [row["wallet__user__email"] for row in resp.context["top_bloggers"]]
        self.assertIn("blog@test.com", emails)
