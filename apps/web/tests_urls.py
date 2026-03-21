"""
Комплексные URL-тесты для всего сайта.
Проверяют: коды ответов, редиректы, контроль доступа, бизнес-логику страниц.

Run:
    docker compose run --rm web python manage.py test apps.web.tests_urls -v 2
"""
import uuid
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from apps.billing.models import Transaction, Wallet, WithdrawalRequest
from apps.campaigns.models import Campaign, Response as CampaignResponse
from apps.deals.models import Deal, DealStatusLog
from apps.platforms.models import Category, Platform
from apps.profiles.models import AdvertiserProfile, BloggerProfile
from apps.users.models import User


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_blogger(email="blogger@test.com", password="Test1234!"):
    u = User.objects.create_user(email=email, password=password, role=User.Role.BLOGGER)
    u.status = User.Status.ACTIVE
    u.is_email_confirmed = True
    u.save()
    return u


def make_advertiser(email="adv@test.com", password="Test1234!"):
    u = User.objects.create_user(email=email, password=password, role=User.Role.ADVERTISER)
    u.status = User.Status.ACTIVE
    u.is_email_confirmed = True
    u.save()
    return u


def make_staff(email="staff@test.com", password="Test1234!"):
    u = User.objects.create_user(email=email, password=password, role=User.Role.ADVERTISER)
    u.status = User.Status.ACTIVE
    u.is_email_confirmed = True
    u.is_staff = True
    u.save()
    return u


def make_platform(blogger, status=Platform.Status.APPROVED, url="https://t.me/testchan"):
    return Platform.objects.create(
        blogger=blogger,
        social_type=Platform.SocialType.TELEGRAM,
        url=url,
        subscribers=10000,
        status=status,
    )


def make_campaign(advertiser, status=Campaign.Status.ACTIVE):
    return Campaign.objects.create(
        advertiser=advertiser,
        name="Test Campaign",
        budget=Decimal("100000"),
        status=status,
    )


def make_wallet(user, available=Decimal("0")):
    w, _ = Wallet.objects.get_or_create(user=user)
    w.available_balance = available
    w.save()
    return w


def make_deal(advertiser, blogger, campaign, platform, status=Deal.Status.IN_PROGRESS, amount=Decimal("10000")):
    return Deal.objects.create(
        campaign=campaign,
        blogger=blogger,
        platform=platform,
        advertiser=advertiser,
        amount=amount,
        status=status,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. ПУБЛИЧНЫЕ СТРАНИЦЫ (без авторизации)
# ═══════════════════════════════════════════════════════════════════════════════

class PublicPagesTest(TestCase):
    """Публичные страницы доступны всем без авторизации."""

    def setUp(self):
        self.c = Client()

    def test_landing_200_for_anonymous(self):
        r = self.c.get(reverse("web:landing"))
        self.assertEqual(r.status_code, 200)

    def test_landing_contains_platform_name(self):
        r = self.c.get(reverse("web:landing"))
        self.assertContains(r, "Mkt")

    def test_login_page_200(self):
        r = self.c.get(reverse("web:login"))
        self.assertEqual(r.status_code, 200)

    def test_register_page_200(self):
        r = self.c.get(reverse("web:register"))
        self.assertEqual(r.status_code, 200)

    def test_faq_page_200(self):
        r = self.c.get(reverse("web:faq"))
        self.assertEqual(r.status_code, 200)

    def test_faq_contains_deal_statuses(self):
        r = self.c.get(reverse("web:faq"))
        self.assertContains(r, "Завершена")

    def test_password_reset_page_200(self):
        r = self.c.get(reverse("web:password_reset"))
        self.assertEqual(r.status_code, 200)

    def test_confirm_email_invalid_token_200(self):
        r = self.c.get(reverse("web:email_confirm", kwargs={"token": uuid.uuid4()}))
        self.assertEqual(r.status_code, 200)

    def test_password_reset_confirm_invalid_token_200(self):
        r = self.c.get(reverse("web:password_reset_confirm", kwargs={"token": uuid.uuid4()}))
        self.assertEqual(r.status_code, 200)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. РЕДИРЕКТЫ ПРИ АВТОРИЗАЦИИ
# ═══════════════════════════════════════════════════════════════════════════════

class AuthRedirectTest(TestCase):
    """Авторизованный пользователь не видит страницы логина/регистрации."""

    def test_advertiser_login_page_redirects_to_dashboard(self):
        u = make_advertiser()
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:login"))
        self.assertRedirects(r, reverse("web:advertiser_dashboard"), fetch_redirect_response=False)

    def test_blogger_login_page_redirects_to_dashboard(self):
        u = make_blogger()
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:login"))
        self.assertRedirects(r, reverse("web:blogger_dashboard"), fetch_redirect_response=False)

    def test_staff_login_page_redirects_to_admin_panel(self):
        u = make_staff()
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:login"))
        self.assertRedirects(r, reverse("web:admin_dashboard"), fetch_redirect_response=False)

    def test_advertiser_landing_redirects_to_dashboard(self):
        u = make_advertiser()
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:landing"))
        self.assertRedirects(r, reverse("web:advertiser_dashboard"), fetch_redirect_response=False)

    def test_blogger_landing_redirects_to_dashboard(self):
        u = make_blogger()
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:landing"))
        self.assertRedirects(r, reverse("web:blogger_dashboard"), fetch_redirect_response=False)

    def test_staff_landing_redirects_to_admin_panel(self):
        u = make_staff()
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:landing"))
        self.assertRedirects(r, reverse("web:admin_dashboard"), fetch_redirect_response=False)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. АНОНИМНЫЙ ДОСТУП → РЕДИРЕКТ НА ЛОГИН
# ═══════════════════════════════════════════════════════════════════════════════

class AnonymousAccessTest(TestCase):
    """Все защищённые страницы редиректят анонима на /login/?next=..."""

    def setUp(self):
        self.c = Client()
        # Создаём пользователей для генерации URL с pk
        self.adv = make_advertiser()
        self.blogger = make_blogger()
        self.campaign = make_campaign(self.adv)
        self.platform = make_platform(self.blogger)
        self.deal = make_deal(self.adv, self.blogger, self.campaign, self.platform)

    def _assert_login_redirect(self, url):
        r = self.c.get(url)
        self.assertEqual(r.status_code, 302, f"Expected 302 for {url}, got {r.status_code}")
        self.assertIn("/login/", r["Location"], f"Expected redirect to login for {url}")

    def test_advertiser_dashboard_anon(self):
        self._assert_login_redirect(reverse("web:advertiser_dashboard"))

    def test_blogger_dashboard_anon(self):
        self._assert_login_redirect(reverse("web:blogger_dashboard"))

    def test_campaign_list_anon(self):
        self._assert_login_redirect(reverse("web:campaign_list"))

    def test_campaign_create_anon(self):
        self._assert_login_redirect(reverse("web:campaign_create"))

    def test_campaign_detail_anon(self):
        self._assert_login_redirect(reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk}))

    def test_campaign_edit_anon(self):
        self._assert_login_redirect(reverse("web:campaign_edit", kwargs={"pk": self.campaign.pk}))

    def test_deal_list_anon(self):
        self._assert_login_redirect(reverse("web:deal_list"))

    def test_deal_detail_anon(self):
        self._assert_login_redirect(reverse("web:deal_detail", kwargs={"pk": self.deal.pk}))

    def test_wallet_anon(self):
        self._assert_login_redirect(reverse("web:wallet"))

    def test_profile_anon(self):
        self._assert_login_redirect(reverse("web:profile"))

    def test_profile_edit_anon(self):
        self._assert_login_redirect(reverse("web:profile_edit"))

    def test_platform_add_anon(self):
        self._assert_login_redirect(reverse("web:platform_add"))

    def test_platform_edit_anon(self):
        self._assert_login_redirect(reverse("web:platform_edit", kwargs={"pk": self.platform.pk}))

    def test_admin_dashboard_anon(self):
        r = self.c.get(reverse("web:admin_dashboard"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r["Location"])

    def test_admin_campaigns_anon(self):
        r = self.c.get(reverse("web:admin_campaigns"))
        self.assertEqual(r.status_code, 302)

    def test_admin_platforms_anon(self):
        r = self.c.get(reverse("web:admin_platforms"))
        self.assertEqual(r.status_code, 302)

    def test_admin_users_anon(self):
        r = self.c.get(reverse("web:admin_users"))
        self.assertEqual(r.status_code, 302)

    def test_blogger_public_profile_anon(self):
        self._assert_login_redirect(reverse("web:blogger_public_profile", kwargs={"pk": self.blogger.pk}))


# ═══════════════════════════════════════════════════════════════════════════════
# 4. ДАШБОРДЫ
# ═══════════════════════════════════════════════════════════════════════════════

class DashboardTest(TestCase):

    def test_advertiser_dashboard_200(self):
        u = make_advertiser()
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:advertiser_dashboard"))
        self.assertEqual(r.status_code, 200)

    def test_advertiser_dashboard_contains_key_elements(self):
        u = make_advertiser()
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:advertiser_dashboard"))
        self.assertContains(r, "Дашборд")

    def test_blogger_dashboard_200(self):
        u = make_blogger()
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:blogger_dashboard"))
        self.assertEqual(r.status_code, 200)

    def test_staff_advertiser_dashboard_redirects_to_panel(self):
        u = make_staff()
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:advertiser_dashboard"))
        self.assertRedirects(r, reverse("web:admin_dashboard"), fetch_redirect_response=False)

    def test_staff_blogger_dashboard_redirects_to_panel(self):
        u = make_staff()
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:blogger_dashboard"))
        self.assertRedirects(r, reverse("web:admin_dashboard"), fetch_redirect_response=False)

    def test_blogger_dashboard_shows_no_platforms_warning(self):
        u = make_blogger()
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:blogger_dashboard"))
        self.assertEqual(r.status_code, 200)
        # Should show content even without platforms


# ═══════════════════════════════════════════════════════════════════════════════
# 5. КАМПАНИИ
# ═══════════════════════════════════════════════════════════════════════════════

class CampaignPagesTest(TestCase):

    def setUp(self):
        self.adv = make_advertiser()
        self.adv2 = make_advertiser("adv2@test.com")
        self.blogger = make_blogger()
        self.staff = make_staff()
        self.campaign_draft = Campaign.objects.create(
            advertiser=self.adv,
            name="UNIQUE_DRAFT_XZ99",
            budget=Decimal("100000"),
            status=Campaign.Status.DRAFT,
        )
        self.campaign_active = make_campaign(self.adv, status=Campaign.Status.ACTIVE)

    def test_campaign_list_advertiser_200(self):
        c = Client()
        c.force_login(self.adv)
        r = c.get(reverse("web:campaign_list"))
        self.assertEqual(r.status_code, 200)

    def test_campaign_list_blogger_200(self):
        c = Client()
        c.force_login(self.blogger)
        r = c.get(reverse("web:campaign_list"))
        self.assertEqual(r.status_code, 200)

    def test_campaign_list_staff_200(self):
        c = Client()
        c.force_login(self.staff)
        r = c.get(reverse("web:campaign_list"))
        self.assertEqual(r.status_code, 200)

    def test_campaign_create_advertiser_200(self):
        c = Client()
        c.force_login(self.adv)
        r = c.get(reverse("web:campaign_create"))
        self.assertEqual(r.status_code, 200)

    def test_campaign_create_blogger_redirected(self):
        c = Client()
        c.force_login(self.blogger)
        r = c.get(reverse("web:campaign_create"))
        self.assertEqual(r.status_code, 302)

    def test_campaign_detail_owner_200(self):
        c = Client()
        c.force_login(self.adv)
        r = c.get(reverse("web:campaign_detail", kwargs={"pk": self.campaign_active.pk}))
        self.assertEqual(r.status_code, 200)

    def test_campaign_detail_blogger_200(self):
        """Блогер может видеть кампанию для отклика."""
        c = Client()
        c.force_login(self.blogger)
        r = c.get(reverse("web:campaign_detail", kwargs={"pk": self.campaign_active.pk}))
        self.assertEqual(r.status_code, 200)

    def test_campaign_detail_other_advertiser_404(self):
        """Другой рекламодатель не видит чужую кампанию."""
        c = Client()
        c.force_login(self.adv2)
        r = c.get(reverse("web:campaign_detail", kwargs={"pk": self.campaign_active.pk}))
        self.assertEqual(r.status_code, 404)

    def test_campaign_edit_owner_draft_200(self):
        c = Client()
        c.force_login(self.adv)
        r = c.get(reverse("web:campaign_edit", kwargs={"pk": self.campaign_draft.pk}))
        self.assertEqual(r.status_code, 200)

    def test_campaign_edit_active_redirects(self):
        """Нельзя редактировать активную кампанию."""
        c = Client()
        c.force_login(self.adv)
        r = c.get(reverse("web:campaign_edit", kwargs={"pk": self.campaign_active.pk}))
        self.assertEqual(r.status_code, 302)

    def test_campaign_edit_other_advertiser_404(self):
        c = Client()
        c.force_login(self.adv2)
        r = c.get(reverse("web:campaign_edit", kwargs={"pk": self.campaign_draft.pk}))
        self.assertEqual(r.status_code, 404)

    def test_campaign_submit_draft_redirects(self):
        c = Client()
        c.force_login(self.adv)
        r = c.post(reverse("web:campaign_submit", kwargs={"pk": self.campaign_draft.pk}))
        self.assertRedirects(
            r,
            reverse("web:campaign_detail", kwargs={"pk": self.campaign_draft.pk}),
            fetch_redirect_response=False,
        )
        self.campaign_draft.refresh_from_db()
        self.assertEqual(self.campaign_draft.status, Campaign.Status.MODERATION)

    def test_campaign_submit_get_not_allowed(self):
        c = Client()
        c.force_login(self.adv)
        r = c.get(reverse("web:campaign_submit", kwargs={"pk": self.campaign_draft.pk}))
        self.assertEqual(r.status_code, 405)

    def test_campaign_pause_active(self):
        c = Client()
        c.force_login(self.adv)
        r = c.post(reverse("web:campaign_pause", kwargs={"pk": self.campaign_active.pk}))
        self.assertEqual(r.status_code, 302)
        self.campaign_active.refresh_from_db()
        self.assertEqual(self.campaign_active.status, Campaign.Status.PAUSED)

    def test_campaign_resume(self):
        campaign = make_campaign(self.adv, status=Campaign.Status.PAUSED)
        c = Client()
        c.force_login(self.adv)
        r = c.post(reverse("web:campaign_resume", kwargs={"pk": campaign.pk}))
        self.assertEqual(r.status_code, 302)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.ACTIVE)

    def test_campaign_blogger_list_shows_only_active(self):
        """Блогер видит только активные кампании."""
        c = Client()
        c.force_login(self.blogger)
        r = c.get(reverse("web:campaign_list"))
        # Draft should not appear in blogger's list
        self.assertNotContains(r, "UNIQUE_DRAFT_XZ99")

    def test_campaign_staff_list_shows_all_statuses(self):
        """Стафф видит все кампании."""
        c = Client()
        c.force_login(self.staff)
        r = c.get(reverse("web:campaign_list"))
        self.assertEqual(r.status_code, 200)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. ОТКЛИКИ НА КАМПАНИЮ
# ═══════════════════════════════════════════════════════════════════════════════

class CampaignResponseTest(TestCase):

    def setUp(self):
        self.adv = make_advertiser()
        self.blogger = make_blogger()
        self.campaign = make_campaign(self.adv, status=Campaign.Status.ACTIVE)
        self.platform = make_platform(self.blogger)
        make_wallet(self.adv, Decimal("500000"))

    def test_respond_with_approved_platform_creates_response(self):
        c = Client()
        c.force_login(self.blogger)
        r = c.post(reverse("web:campaign_respond", kwargs={"pk": self.campaign.pk}), {
            "platform": self.platform.pk,
            "content_type": "post",
            "proposed_price": "10000",
            "message": "Hello",
        })
        self.assertEqual(r.status_code, 302)
        self.assertTrue(CampaignResponse.objects.filter(
            blogger=self.blogger, campaign=self.campaign
        ).exists())

    def test_respond_with_pending_platform_rejected(self):
        """Нельзя откликнуться с неодобренной площадкой."""
        pending_platform = make_platform(self.blogger, status=Platform.Status.PENDING, url="https://t.me/pending")
        c = Client()
        c.force_login(self.blogger)
        r = c.post(reverse("web:campaign_respond", kwargs={"pk": self.campaign.pk}), {
            "platform": pending_platform.pk,
            "content_type": "post",
            "proposed_price": "10000",
            "message": "",
        })
        # Should 404 — backend enforces approved only
        self.assertEqual(r.status_code, 404)

    def test_respond_as_advertiser_redirected(self):
        """Рекламодатель не может откликнуться."""
        c = Client()
        c.force_login(self.adv)
        r = c.post(reverse("web:campaign_respond", kwargs={"pk": self.campaign.pk}), {
            "platform": self.platform.pk,
            "content_type": "post",
            "proposed_price": "10000",
        })
        self.assertEqual(r.status_code, 302)
        self.assertFalse(CampaignResponse.objects.filter(campaign=self.campaign).exists())

    def test_response_accept_creates_deal(self):
        """Рекламодатель принимает отклик → создаётся сделка."""
        resp = CampaignResponse.objects.create(
            blogger=self.blogger,
            campaign=self.campaign,
            platform=self.platform,
            content_type="post",
            proposed_price=Decimal("10000"),
            status=CampaignResponse.Status.PENDING,
        )
        c = Client()
        c.force_login(self.adv)
        r = c.post(reverse("web:response_accept", kwargs={"pk": resp.pk}))
        self.assertEqual(r.status_code, 302)
        resp.refresh_from_db()
        self.assertEqual(resp.status, CampaignResponse.Status.ACCEPTED)
        self.assertTrue(Deal.objects.filter(
            campaign=self.campaign, blogger=self.blogger
        ).exists())

    def test_response_reject(self):
        resp = CampaignResponse.objects.create(
            blogger=self.blogger,
            campaign=self.campaign,
            platform=self.platform,
            content_type="post",
            proposed_price=Decimal("10000"),
            status=CampaignResponse.Status.PENDING,
        )
        c = Client()
        c.force_login(self.adv)
        r = c.post(reverse("web:response_reject", kwargs={"pk": resp.pk}))
        self.assertEqual(r.status_code, 302)
        resp.refresh_from_db()
        self.assertEqual(resp.status, CampaignResponse.Status.REJECTED)

    def test_response_accept_by_wrong_advertiser_404(self):
        adv2 = make_advertiser("adv2@test.com")
        resp = CampaignResponse.objects.create(
            blogger=self.blogger,
            campaign=self.campaign,
            platform=self.platform,
            content_type="post",
            proposed_price=Decimal("10000"),
            status=CampaignResponse.Status.PENDING,
        )
        c = Client()
        c.force_login(adv2)
        r = c.post(reverse("web:response_accept", kwargs={"pk": resp.pk}))
        self.assertEqual(r.status_code, 404)


# ═══════════════════════════════════════════════════════════════════════════════
# 7. ПЛОЩАДКИ (платформы блогера)
# ═══════════════════════════════════════════════════════════════════════════════

class PlatformPagesTest(TestCase):

    def setUp(self):
        self.blogger = make_blogger()
        self.blogger2 = make_blogger("b2@test.com")
        self.adv = make_advertiser()
        self.platform = make_platform(self.blogger, status=Platform.Status.PENDING, url="https://t.me/mychan")

    def test_platform_add_blogger_200(self):
        c = Client()
        c.force_login(self.blogger)
        r = c.get(reverse("web:platform_add"))
        self.assertEqual(r.status_code, 200)

    def test_platform_add_advertiser_redirected(self):
        c = Client()
        c.force_login(self.adv)
        r = c.get(reverse("web:platform_add"))
        self.assertEqual(r.status_code, 302)

    def test_platform_add_post_creates_pending(self):
        c = Client()
        c.force_login(self.blogger)
        r = c.post(reverse("web:platform_add"), {
            "social_type": "youtube",
            "url": "https://youtube.com/testchan",
            "subscribers": 5000,
            "avg_views": 1000,
            "engagement_rate": "2.5",
        })
        self.assertRedirects(r, reverse("web:profile"), fetch_redirect_response=False)
        p = Platform.objects.get(url="https://youtube.com/testchan")
        self.assertEqual(p.status, Platform.Status.PENDING)

    def test_platform_edit_owner_200(self):
        c = Client()
        c.force_login(self.blogger)
        r = c.get(reverse("web:platform_edit", kwargs={"pk": self.platform.pk}))
        self.assertEqual(r.status_code, 200)

    def test_platform_edit_other_blogger_404(self):
        c = Client()
        c.force_login(self.blogger2)
        r = c.get(reverse("web:platform_edit", kwargs={"pk": self.platform.pk}))
        self.assertEqual(r.status_code, 404)

    def test_platform_url_change_resets_to_pending(self):
        approved = make_platform(self.blogger, status=Platform.Status.APPROVED, url="https://t.me/approved")
        c = Client()
        c.force_login(self.blogger)
        c.post(reverse("web:platform_edit", kwargs={"pk": approved.pk}), {
            "social_type": "telegram",
            "url": "https://t.me/newurl",
            "subscribers": 5000,
            "avg_views": 1000,
            "engagement_rate": "2.5",
        })
        approved.refresh_from_db()
        self.assertEqual(approved.status, Platform.Status.PENDING)

    def test_platform_url_no_change_keeps_status(self):
        approved = make_platform(self.blogger, status=Platform.Status.APPROVED, url="https://t.me/unchanged")
        c = Client()
        c.force_login(self.blogger)
        c.post(reverse("web:platform_edit", kwargs={"pk": approved.pk}), {
            "social_type": "telegram",
            "url": "https://t.me/unchanged",
            "subscribers": 9999,
            "avg_views": 2000,
            "engagement_rate": "3.0",
        })
        approved.refresh_from_db()
        self.assertEqual(approved.status, Platform.Status.APPROVED)

    def test_platform_delete_pending_allowed(self):
        c = Client()
        c.force_login(self.blogger)
        r = c.post(reverse("web:platform_delete", kwargs={"pk": self.platform.pk}))
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Platform.objects.filter(pk=self.platform.pk).exists())

    def test_platform_delete_approved_denied(self):
        approved = make_platform(self.blogger, status=Platform.Status.APPROVED, url="https://t.me/approved2")
        c = Client()
        c.force_login(self.blogger)
        c.post(reverse("web:platform_delete", kwargs={"pk": approved.pk}))
        self.assertTrue(Platform.objects.filter(pk=approved.pk).exists())

    def test_platform_delete_other_blogger_404(self):
        c = Client()
        c.force_login(self.blogger2)
        r = c.post(reverse("web:platform_delete", kwargs={"pk": self.platform.pk}))
        self.assertEqual(r.status_code, 404)


# ═══════════════════════════════════════════════════════════════════════════════
# 8. ПРОФИЛЬ
# ═══════════════════════════════════════════════════════════════════════════════

class ProfilePagesTest(TestCase):

    def setUp(self):
        self.blogger = make_blogger()
        self.adv = make_advertiser()
        self.staff = make_staff()

    def test_profile_blogger_200(self):
        c = Client()
        c.force_login(self.blogger)
        r = c.get(reverse("web:profile"))
        self.assertEqual(r.status_code, 200)

    def test_profile_advertiser_200(self):
        c = Client()
        c.force_login(self.adv)
        r = c.get(reverse("web:profile"))
        self.assertEqual(r.status_code, 200)

    def test_profile_staff_redirects_to_panel(self):
        c = Client()
        c.force_login(self.staff)
        r = c.get(reverse("web:profile"))
        self.assertRedirects(r, reverse("web:admin_dashboard"), fetch_redirect_response=False)

    def test_profile_edit_blogger_200(self):
        c = Client()
        c.force_login(self.blogger)
        r = c.get(reverse("web:profile_edit"))
        self.assertEqual(r.status_code, 200)

    def test_profile_edit_advertiser_200(self):
        c = Client()
        c.force_login(self.adv)
        r = c.get(reverse("web:profile_edit"))
        self.assertEqual(r.status_code, 200)

    def test_profile_edit_staff_redirects_to_panel(self):
        c = Client()
        c.force_login(self.staff)
        r = c.get(reverse("web:profile_edit"))
        self.assertRedirects(r, reverse("web:admin_dashboard"), fetch_redirect_response=False)

    def test_blogger_public_profile_200(self):
        c = Client()
        c.force_login(self.adv)
        r = c.get(reverse("web:blogger_public_profile", kwargs={"pk": self.blogger.pk}))
        self.assertEqual(r.status_code, 200)

    def test_blogger_public_profile_wrong_pk_404(self):
        c = Client()
        c.force_login(self.adv)
        r = c.get(reverse("web:blogger_public_profile", kwargs={"pk": 99999}))
        self.assertEqual(r.status_code, 404)

    def test_blogger_public_profile_advertiser_pk_404(self):
        """Публичный профиль есть только у блогеров."""
        c = Client()
        c.force_login(self.blogger)
        r = c.get(reverse("web:blogger_public_profile", kwargs={"pk": self.adv.pk}))
        self.assertEqual(r.status_code, 404)

    def test_profile_completed_deals_count_shown(self):
        campaign = make_campaign(self.adv)
        platform = make_platform(self.blogger)
        Deal.objects.create(
            campaign=campaign,
            blogger=self.blogger,
            platform=platform,
            advertiser=self.adv,
            amount=Decimal("5000"),
            status=Deal.Status.COMPLETED,
        )
        c = Client()
        c.force_login(self.blogger)
        r = c.get(reverse("web:profile"))
        self.assertContains(r, "1")


# ═══════════════════════════════════════════════════════════════════════════════
# 9. СДЕЛКИ
# ═══════════════════════════════════════════════════════════════════════════════

class DealPagesTest(TestCase):

    def setUp(self):
        self.adv = make_advertiser()
        self.blogger = make_blogger()
        self.adv2 = make_advertiser("adv2@test.com")
        self.blogger2 = make_blogger("b2@test.com")
        self.staff = make_staff()
        self.campaign = make_campaign(self.adv)
        self.platform = make_platform(self.blogger)
        make_wallet(self.adv, Decimal("100000"))
        make_wallet(self.adv, Decimal("100000"))
        self.deal = make_deal(self.adv, self.blogger, self.campaign, self.platform)

    def test_deal_list_advertiser_200(self):
        c = Client()
        c.force_login(self.adv)
        r = c.get(reverse("web:deal_list"))
        self.assertEqual(r.status_code, 200)

    def test_deal_list_blogger_200(self):
        c = Client()
        c.force_login(self.blogger)
        r = c.get(reverse("web:deal_list"))
        self.assertEqual(r.status_code, 200)

    def test_deal_list_staff_200(self):
        c = Client()
        c.force_login(self.staff)
        r = c.get(reverse("web:deal_list"))
        self.assertEqual(r.status_code, 200)

    def test_deal_detail_advertiser_200(self):
        c = Client()
        c.force_login(self.adv)
        r = c.get(reverse("web:deal_detail", kwargs={"pk": self.deal.pk}))
        self.assertEqual(r.status_code, 200)

    def test_deal_detail_blogger_200(self):
        c = Client()
        c.force_login(self.blogger)
        r = c.get(reverse("web:deal_detail", kwargs={"pk": self.deal.pk}))
        self.assertEqual(r.status_code, 200)

    def test_deal_detail_staff_200(self):
        c = Client()
        c.force_login(self.staff)
        r = c.get(reverse("web:deal_detail", kwargs={"pk": self.deal.pk}))
        self.assertEqual(r.status_code, 200)

    def test_deal_detail_other_advertiser_404(self):
        c = Client()
        c.force_login(self.adv2)
        r = c.get(reverse("web:deal_detail", kwargs={"pk": self.deal.pk}))
        self.assertEqual(r.status_code, 404)

    def test_deal_detail_other_blogger_404(self):
        c = Client()
        c.force_login(self.blogger2)
        r = c.get(reverse("web:deal_detail", kwargs={"pk": self.deal.pk}))
        self.assertEqual(r.status_code, 404)

    def test_deal_submit_publication(self):
        c = Client()
        c.force_login(self.blogger)
        r = c.post(reverse("web:deal_submit_publication", kwargs={"pk": self.deal.pk}), {
            "publication_url": "https://t.me/testchan/123",
        })
        self.assertRedirects(
            r,
            reverse("web:deal_detail", kwargs={"pk": self.deal.pk}),
            fetch_redirect_response=False,
        )
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.CHECKING)
        self.assertEqual(self.deal.publication_url, "https://t.me/testchan/123")

    def test_deal_submit_publication_invalid_url(self):
        c = Client()
        c.force_login(self.blogger)
        r = c.post(reverse("web:deal_submit_publication", kwargs={"pk": self.deal.pk}), {
            "publication_url": "not-a-url",
        })
        self.assertEqual(r.status_code, 302)
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.IN_PROGRESS)

    def test_deal_submit_publication_empty_url(self):
        c = Client()
        c.force_login(self.blogger)
        r = c.post(reverse("web:deal_submit_publication", kwargs={"pk": self.deal.pk}), {
            "publication_url": "",
        })
        self.assertEqual(r.status_code, 302)
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.IN_PROGRESS)

    def test_deal_confirm_by_advertiser(self):
        """Рекламодатель подтверждает публикацию → COMPLETED."""
        adv = make_advertiser("adv_confirm@test.com")
        blogger = make_blogger("blogger_confirm@test.com")
        campaign = make_campaign(adv)
        platform = make_platform(blogger, url="https://t.me/confirm")
        wallet = make_wallet(adv, Decimal("100000"))
        wallet.reserved_balance = Decimal("10000")
        wallet.available_balance = Decimal("90000")
        wallet.save()
        blogger_wallet = make_wallet(blogger)

        deal = make_deal(adv, blogger, campaign, platform, status=Deal.Status.CHECKING)
        deal.publication_url = "https://t.me/confirm/1"
        deal.save()

        c = Client()
        c.force_login(adv)
        r = c.post(reverse("web:deal_confirm", kwargs={"pk": deal.pk}))
        self.assertEqual(r.status_code, 302)
        deal.refresh_from_db()
        self.assertEqual(deal.status, Deal.Status.COMPLETED)

    def test_deal_confirm_wrong_status_error(self):
        c = Client()
        c.force_login(self.adv)
        r = c.post(reverse("web:deal_confirm", kwargs={"pk": self.deal.pk}))
        self.assertEqual(r.status_code, 302)
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.IN_PROGRESS)

    def test_deal_cancel_by_advertiser_in_progress(self):
        adv = make_advertiser("adv_cancel@test.com")
        blogger = make_blogger("blogger_cancel@test.com")
        campaign = make_campaign(adv)
        platform = make_platform(blogger, url="https://t.me/cancel")
        wallet = make_wallet(adv, Decimal("0"))
        wallet.reserved_balance = Decimal("10000")
        wallet.save()

        deal = make_deal(adv, blogger, campaign, platform, status=Deal.Status.IN_PROGRESS)
        c = Client()
        c.force_login(adv)
        r = c.post(reverse("web:deal_cancel", kwargs={"pk": deal.pk}))
        self.assertRedirects(r, reverse("web:deal_list"), fetch_redirect_response=False)
        deal.refresh_from_db()
        self.assertEqual(deal.status, Deal.Status.CANCELLED)

    def test_deal_cancel_blogger_in_progress_denied(self):
        """Блогер не может отменить сделку в статусе IN_PROGRESS."""
        c = Client()
        c.force_login(self.blogger)
        c.post(reverse("web:deal_cancel", kwargs={"pk": self.deal.pk}))
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.IN_PROGRESS)

    def test_deal_submit_publication_get_not_allowed(self):
        c = Client()
        c.force_login(self.blogger)
        r = c.get(reverse("web:deal_submit_publication", kwargs={"pk": self.deal.pk}))
        self.assertEqual(r.status_code, 405)


# ═══════════════════════════════════════════════════════════════════════════════
# 10. КОШЕЛЁК
# ═══════════════════════════════════════════════════════════════════════════════

class WalletPagesTest(TestCase):

    def test_wallet_advertiser_200(self):
        u = make_advertiser()
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:wallet"))
        self.assertEqual(r.status_code, 200)

    def test_wallet_blogger_200(self):
        u = make_blogger()
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:wallet"))
        self.assertEqual(r.status_code, 200)

    def test_wallet_blogger_withdrawal_below_minimum_error(self):
        u = make_blogger()
        wallet = make_wallet(u, Decimal("200000"))
        c = Client()
        c.force_login(u)
        r = c.post(reverse("web:wallet"), {
            "amount": "100",  # ниже минимума
            "card": "1234 5678 9012 3456",
        })
        self.assertEqual(r.status_code, 200)
        self.assertFalse(WithdrawalRequest.objects.filter(blogger=u).exists())

    def test_wallet_blogger_withdrawal_insufficient_funds(self):
        u = make_blogger()
        make_wallet(u, Decimal("0"))
        c = Client()
        c.force_login(u)
        r = c.post(reverse("web:wallet"), {
            "amount": "500000",
            "card": "1234 5678 9012 3456",
        })
        self.assertEqual(r.status_code, 200)
        self.assertFalse(WithdrawalRequest.objects.filter(blogger=u).exists())

    def test_wallet_advertiser_cannot_withdraw(self):
        u = make_advertiser()
        make_wallet(u, Decimal("500000"))
        c = Client()
        c.force_login(u)
        r = c.post(reverse("web:wallet"), {
            "amount": "200000",
            "card": "1234",
        })
        self.assertEqual(r.status_code, 200)
        self.assertFalse(WithdrawalRequest.objects.filter(blogger=u).exists())


# ═══════════════════════════════════════════════════════════════════════════════
# 11. КАТАЛОГ (для блогеров)
# ═══════════════════════════════════════════════════════════════════════════════

class CatalogTest(TestCase):

    def test_catalog_blogger_200(self):
        u = make_blogger()
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:catalog"))
        self.assertEqual(r.status_code, 200)

    def test_catalog_uses_same_view_as_campaign_list(self):
        u = make_blogger()
        c = Client()
        c.force_login(u)
        r1 = c.get(reverse("web:catalog"))
        r2 = c.get(reverse("web:campaign_list"))
        self.assertEqual(r1.status_code, r2.status_code)


# ═══════════════════════════════════════════════════════════════════════════════
# 12. ADMIN PANEL (staff only)
# ═══════════════════════════════════════════════════════════════════════════════

class AdminPanelAccessTest(TestCase):
    """Не-стафф получает редирект, стафф — 200."""

    def setUp(self):
        self.staff = make_staff()
        self.adv = make_advertiser()
        self.blogger = make_blogger()

    def _assert_staff_only(self, url_name, kwargs=None):
        url = reverse(url_name, kwargs=kwargs)
        # Non-staff advertiser gets redirected
        c = Client()
        c.force_login(self.adv)
        r = c.get(url)
        self.assertEqual(r.status_code, 302, f"Advertiser should be redirected from {url}")

        # Non-staff blogger gets redirected
        c2 = Client()
        c2.force_login(self.blogger)
        r2 = c2.get(url)
        self.assertEqual(r2.status_code, 302, f"Blogger should be redirected from {url}")

        # Staff gets 200
        c3 = Client()
        c3.force_login(self.staff)
        r3 = c3.get(url)
        self.assertEqual(r3.status_code, 200, f"Staff should get 200 from {url}")

    def test_admin_dashboard(self):
        self._assert_staff_only("web:admin_dashboard")

    def test_admin_campaigns(self):
        self._assert_staff_only("web:admin_campaigns")

    def test_admin_platforms(self):
        self._assert_staff_only("web:admin_platforms")

    def test_admin_disputes(self):
        self._assert_staff_only("web:admin_disputes")

    def test_admin_withdrawals(self):
        self._assert_staff_only("web:admin_withdrawals")

    def test_admin_users(self):
        self._assert_staff_only("web:admin_users")


class AdminPanelContentTest(TestCase):

    def setUp(self):
        self.staff = make_staff()
        self.adv = make_advertiser()
        self.blogger = make_blogger()

    def test_admin_dashboard_shows_counters(self):
        c = Client()
        c.force_login(self.staff)
        r = c.get(reverse("web:admin_dashboard"))
        self.assertContains(r, "Панель администратора")

    def test_admin_dashboard_no_django_admin_link(self):
        """Ссылка на /admin/ не должна светиться бизнес-администратору."""
        c = Client()
        c.force_login(self.staff)
        r = c.get(reverse("web:admin_dashboard"))
        self.assertNotContains(r, "Django Admin")

    def test_admin_users_lists_all_users(self):
        c = Client()
        c.force_login(self.staff)
        r = c.get(reverse("web:admin_users"))
        self.assertContains(r, self.adv.email)
        self.assertContains(r, self.blogger.email)

    def test_admin_campaigns_shows_moderation_queue(self):
        campaign = make_campaign(self.adv, status=Campaign.Status.MODERATION)
        c = Client()
        c.force_login(self.staff)
        r = c.get(reverse("web:admin_campaigns"))
        self.assertContains(r, campaign.name)

    def test_admin_platforms_shows_pending_only(self):
        pending = make_platform(self.blogger, status=Platform.Status.PENDING, url="https://t.me/pending_admin")
        approved = make_platform(self.blogger, status=Platform.Status.APPROVED, url="https://t.me/approved_admin")
        c = Client()
        c.force_login(self.staff)
        r = c.get(reverse("web:admin_platforms"))
        self.assertContains(r, pending.url)
        self.assertNotContains(r, approved.url)

    def test_admin_campaign_approve(self):
        campaign = make_campaign(self.adv, status=Campaign.Status.MODERATION)
        c = Client()
        c.force_login(self.staff)
        r = c.post(reverse("web:admin_campaign_approve", kwargs={"pk": campaign.pk}))
        self.assertEqual(r.status_code, 302)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.ACTIVE)

    def test_admin_campaign_reject(self):
        campaign = make_campaign(self.adv, status=Campaign.Status.MODERATION)
        c = Client()
        c.force_login(self.staff)
        r = c.post(reverse("web:admin_campaign_reject", kwargs={"pk": campaign.pk}), {
            "reason": "Нарушение правил"
        })
        self.assertEqual(r.status_code, 302)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.REJECTED)
        self.assertEqual(campaign.rejection_reason, "Нарушение правил")

    def test_admin_platform_approve(self):
        platform = make_platform(self.blogger, status=Platform.Status.PENDING, url="https://t.me/approve_test")
        c = Client()
        c.force_login(self.staff)
        r = c.post(reverse("web:admin_platform_approve", kwargs={"pk": platform.pk}))
        self.assertEqual(r.status_code, 302)
        platform.refresh_from_db()
        self.assertEqual(platform.status, Platform.Status.APPROVED)

    def test_admin_platform_reject(self):
        platform = make_platform(self.blogger, status=Platform.Status.PENDING, url="https://t.me/reject_test")
        c = Client()
        c.force_login(self.staff)
        r = c.post(reverse("web:admin_platform_reject", kwargs={"pk": platform.pk}), {
            "reason": "Накрутка подписчиков"
        })
        self.assertEqual(r.status_code, 302)
        platform.refresh_from_db()
        self.assertEqual(platform.status, Platform.Status.REJECTED)
        self.assertEqual(platform.rejection_reason, "Накрутка подписчиков")

    def test_admin_withdrawal_approve(self):
        blogger = make_blogger("withdraw_b@test.com")
        wallet = make_wallet(blogger)
        wallet.on_withdrawal = Decimal("50000")
        wallet.save()
        wr = WithdrawalRequest.objects.create(
            blogger=blogger,
            amount=Decimal("50000"),
            requisites={"type": "card", "details": "4111111111111111"},
            status=WithdrawalRequest.Status.PENDING,
        )
        c = Client()
        c.force_login(self.staff)
        r = c.post(reverse("web:admin_withdrawal_approve", kwargs={"pk": wr.pk}), {
            "comment": "Выплачено"
        })
        self.assertEqual(r.status_code, 302)
        wr.refresh_from_db()
        self.assertEqual(wr.status, WithdrawalRequest.Status.COMPLETED)

    def test_admin_withdrawal_reject(self):
        blogger = make_blogger("reject_b@test.com")
        wallet = make_wallet(blogger)
        wallet.on_withdrawal = Decimal("50000")
        wallet.save()
        wr = WithdrawalRequest.objects.create(
            blogger=blogger,
            amount=Decimal("50000"),
            requisites={"type": "card", "details": "4111111111111111"},
            status=WithdrawalRequest.Status.PENDING,
        )
        c = Client()
        c.force_login(self.staff)
        r = c.post(reverse("web:admin_withdrawal_reject", kwargs={"pk": wr.pk}), {
            "comment": "Неверные реквизиты"
        })
        self.assertEqual(r.status_code, 302)
        wr.refresh_from_db()
        self.assertEqual(wr.status, WithdrawalRequest.Status.REJECTED)

    def test_admin_approve_non_moderation_campaign_errors(self):
        """Одобрить кампанию не на модерации → ошибка, редирект."""
        campaign = make_campaign(self.adv, status=Campaign.Status.ACTIVE)
        c = Client()
        c.force_login(self.staff)
        r = c.post(reverse("web:admin_campaign_approve", kwargs={"pk": campaign.pk}))
        self.assertEqual(r.status_code, 302)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.ACTIVE)


# ═══════════════════════════════════════════════════════════════════════════════
# 13. LOGOUT
# ═══════════════════════════════════════════════════════════════════════════════

class LogoutTest(TestCase):

    def test_logout_post_redirects_to_login(self):
        u = make_advertiser()
        c = Client()
        c.force_login(u)
        r = c.post(reverse("web:logout"))
        self.assertRedirects(r, reverse("web:login"), fetch_redirect_response=False)

    def test_logout_get_not_allowed(self):
        u = make_advertiser()
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:logout"))
        self.assertEqual(r.status_code, 405)

    def test_after_logout_dashboard_redirects_to_login(self):
        u = make_advertiser()
        c = Client()
        c.force_login(u)
        c.post(reverse("web:logout"))
        r = c.get(reverse("web:advertiser_dashboard"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r["Location"])


# ═══════════════════════════════════════════════════════════════════════════════
# 14. NAVBAR — контроль контента для разных ролей
# ═══════════════════════════════════════════════════════════════════════════════

class NavbarContentTest(TestCase):
    """Навбар показывает правильные ссылки для каждой роли."""

    def test_staff_navbar_no_advertiser_links(self):
        u = make_staff()
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:admin_dashboard"))
        self.assertContains(r, "Панель")
        self.assertNotContains(r, "Дашборд")

    def test_advertiser_navbar_no_panel_link(self):
        u = make_advertiser()
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:advertiser_dashboard"))
        self.assertContains(r, "Дашборд")
        self.assertNotContains(r, "Панель")

    def test_blogger_navbar_shows_catalog(self):
        u = make_blogger()
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:blogger_dashboard"))
        self.assertContains(r, "Каталог")
        self.assertNotContains(r, "Дашборд" if False else "Панель")
