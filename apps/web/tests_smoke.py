"""
Smoke-тесты по ролям — защита от регрессии.

Проверяем, что каждая ключевая страница отвечает правильным HTTP-статусом
для каждой роли: anonymous, staff, advertiser, blogger.

Тесты НЕ проверяют бизнес-логику — только "страница не падает для данной роли".
Если любой тест красный — регрессия: view упал с ошибкой или права сломаны.

Run: docker compose run --rm web python manage.py test apps.web.tests_smoke -v 2

Добавляя новый view — добавляй строку в соответствующий класс:
    def test_my_new_page(self):
        resp = self._get("my_new_url_name")
        self.assertEqual(resp.status_code, 200)
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import Wallet
from apps.campaigns.models import Campaign
from apps.deals.models import Deal
from apps.platforms.models import Platform

User = get_user_model()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_user(email, role, is_staff=False, password="pass1234"):
    u = User.objects.create_user(email=email, password=password, role=role)
    u.is_active = True
    if is_staff:
        u.is_staff = True
    u.save()
    return u


def _make_wallet(user, balance=500_000):
    w, _ = Wallet.objects.get_or_create(user=user)
    w.balance = Decimal(balance)
    w.save()
    return w


def _make_campaign(advertiser, status=Campaign.Status.ACTIVE):
    return Campaign.objects.create(
        advertiser=advertiser,
        name="Smoke Campaign",
        payment_type=Campaign.PaymentType.FIXED,
        budget=500_000,
        fixed_price=100_000,
        status=status,
        deadline=timezone.now().date() + timedelta(days=30),
    )


def _make_platform(blogger, status=Platform.Status.APPROVED):
    return Platform.objects.create(
        blogger=blogger,
        social_type=Platform.SocialType.TELEGRAM,
        url="https://t.me/smoke_test",
        subscribers=5000,
        status=status,
    )


def _make_deal(advertiser, blogger, campaign, platform,
               status=Deal.Status.IN_PROGRESS, amount=100_000):
    return Deal.objects.create(
        advertiser=advertiser,
        blogger=blogger,
        campaign=campaign,
        platform=platform,
        amount=amount,
        status=status,
    )


# ── Base mixin ────────────────────────────────────────────────────────────────

class _SmokeBase(TestCase):
    """Общие объекты БД для всех smoke-классов (создаются один раз)."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = _make_user("staff@smoke.com", User.Role.ADVERTISER, is_staff=True)
        cls.adv = _make_user("adv@smoke.com", User.Role.ADVERTISER)
        cls.blg = _make_user("blg@smoke.com", User.Role.BLOGGER)

        _make_wallet(cls.staff)
        _make_wallet(cls.adv, 500_000)
        _make_wallet(cls.blg, 0)

        cls.campaign = _make_campaign(cls.adv)
        cls.platform = _make_platform(cls.blg)
        cls.deal = _make_deal(cls.adv, cls.blg, cls.campaign, cls.platform)

    def _get(self, url_name, kwargs=None, client=None):
        """GET-запрос по имени URL в namespace 'web'."""
        c = client or self.client
        url = reverse(f"web:{url_name}", kwargs=kwargs or {})
        return c.get(url)

    def assertOK(self, url_name, kwargs=None, client=None):
        resp = self._get(url_name, kwargs, client)
        self.assertEqual(
            resp.status_code, 200,
            f"Expected 200 for web:{url_name}, got {resp.status_code}"
        )

    def assertRedirect(self, url_name, kwargs=None, client=None):
        resp = self._get(url_name, kwargs, client)
        self.assertEqual(
            resp.status_code, 302,
            f"Expected 302 for web:{url_name}, got {resp.status_code}"
        )

    def assertNotOK(self, url_name, kwargs=None, client=None):
        resp = self._get(url_name, kwargs, client)
        self.assertNotEqual(
            resp.status_code, 200,
            f"Expected non-200 for web:{url_name}, but got 200"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 1. Anonymous
# ══════════════════════════════════════════════════════════════════════════════

class SmokeAnonymousTest(_SmokeBase):
    """
    Публичные страницы → 200.
    Защищённые страницы → 302 (редирект на логин).
    """

    # Публичные — 200

    def test_landing(self):
        self.assertOK("landing")

    def test_faq(self):
        self.assertOK("faq")

    def test_terms(self):
        self.assertOK("terms")

    def test_oferta(self):
        self.assertOK("oferta")

    def test_login(self):
        self.assertOK("login")

    def test_register(self):
        self.assertOK("register")

    # Защищённые — 302

    def test_campaigns_redirects(self):
        self.assertRedirect("campaign_list")

    def test_deals_redirects(self):
        self.assertRedirect("deal_list")

    def test_wallet_redirects(self):
        self.assertRedirect("wallet")

    def test_panel_redirects(self):
        self.assertRedirect("admin_dashboard")

    def test_profile_redirects(self):
        self.assertRedirect("profile")

    def test_analytics_redirects(self):
        self.assertRedirect("analytics")

    def test_notifications_redirects(self):
        self.assertRedirect("notifications")

    def test_permit_list_redirects(self):
        self.assertRedirect("permit_list")

    def test_permit_upload_redirects(self):
        self.assertRedirect("permit_upload")


# ══════════════════════════════════════════════════════════════════════════════
# 2. Staff
# ══════════════════════════════════════════════════════════════════════════════

class SmokeStaffTest(_SmokeBase):
    """
    Staff видит все страницы: и свои, и чужие объекты.
    Это ключевой класс — именно staff-доступ чаще всего ломается.
    """

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.staff)

    # Панель администратора

    def test_admin_dashboard(self):
        self.assertOK("admin_dashboard")

    def test_admin_campaigns(self):
        self.assertOK("admin_campaigns")

    def test_admin_platforms(self):
        self.assertOK("admin_platforms")

    def test_admin_disputes(self):
        self.assertOK("admin_disputes")

    def test_admin_withdrawals(self):
        self.assertOK("admin_withdrawals")

    def test_admin_users(self):
        self.assertOK("admin_users")

    def test_admin_categories(self):
        self.assertOK("admin_categories")

    def test_admin_permits(self):
        self.assertOK("admin_permits")

    # Чужие объекты — staff должен видеть

    def test_campaign_detail_foreign(self):
        """Staff видит кампанию другого пользователя."""
        self.assertOK("campaign_detail", kwargs={"pk": self.campaign.pk})

    def test_deal_detail_foreign(self):
        """Staff видит сделку, в которой он не участвует."""
        self.assertOK("deal_detail", kwargs={"pk": self.deal.pk})

    # Общие страницы

    def test_campaign_list(self):
        self.assertOK("campaign_list")

    def test_deal_list(self):
        self.assertOK("deal_list")

    def test_notifications(self):
        self.assertOK("notifications")

    def test_wallet(self):
        self.assertOK("wallet")

    def test_permit_list(self):
        self.assertOK("permit_list")

    def test_permit_upload(self):
        self.assertOK("permit_upload")


# ══════════════════════════════════════════════════════════════════════════════
# 3. Advertiser
# ══════════════════════════════════════════════════════════════════════════════

class SmokeAdvertiserTest(_SmokeBase):
    """
    Рекламодатель видит свои страницы.
    Не видит admin-панель.
    """

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.adv)

    def test_campaign_list(self):
        self.assertOK("campaign_list")

    def test_campaign_create(self):
        self.assertOK("campaign_create")

    def test_campaign_detail_own(self):
        self.assertOK("campaign_detail", kwargs={"pk": self.campaign.pk})

    def test_deal_list(self):
        self.assertOK("deal_list")

    def test_deal_detail_own(self):
        self.assertOK("deal_detail", kwargs={"pk": self.deal.pk})

    def test_blogger_catalog(self):
        self.assertOK("blogger_catalog")

    def test_analytics(self):
        self.assertOK("analytics")

    def test_wallet(self):
        self.assertOK("wallet")

    def test_notifications(self):
        self.assertOK("notifications")

    def test_profile(self):
        self.assertOK("profile")

    def test_permit_list(self):
        self.assertOK("permit_list")

    def test_permit_upload(self):
        self.assertOK("permit_upload")

    # Запреты

    def test_panel_not_accessible(self):
        self.assertNotOK("admin_dashboard")

    def test_admin_campaigns_not_accessible(self):
        self.assertNotOK("admin_campaigns")

    def test_admin_permits_not_accessible(self):
        self.assertNotOK("admin_permits")


# ══════════════════════════════════════════════════════════════════════════════
# 4. Blogger
# ══════════════════════════════════════════════════════════════════════════════

class SmokeBloggerTest(_SmokeBase):
    """
    Блогер видит свои страницы.
    Не видит admin-панель и каталог блогеров.
    """

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.blg)

    def test_campaign_list(self):
        self.assertOK("campaign_list")

    def test_deal_list(self):
        self.assertOK("deal_list")

    def test_deal_detail_own(self):
        self.assertOK("deal_detail", kwargs={"pk": self.deal.pk})

    def test_platform_add(self):
        self.assertOK("platform_add")

    def test_analytics(self):
        self.assertOK("analytics")

    def test_wallet(self):
        self.assertOK("wallet")

    def test_notifications(self):
        self.assertOK("notifications")

    def test_profile(self):
        self.assertOK("profile")

    def test_permit_list(self):
        self.assertOK("permit_list")

    def test_permit_upload(self):
        self.assertOK("permit_upload")

    # Запреты

    def test_panel_not_accessible(self):
        self.assertNotOK("admin_dashboard")

    def test_blogger_catalog_not_accessible(self):
        """Каталог блогеров — только для рекламодателей."""
        self.assertNotOK("blogger_catalog")

    def test_campaign_create_not_accessible(self):
        """Блогер не может создавать кампании."""
        self.assertNotOK("campaign_create")

    def test_admin_permits_not_accessible(self):
        self.assertNotOK("admin_permits")


# ══════════════════════════════════════════════════════════════════════════════
# 5. Role Guards — межролевые проверки
# ══════════════════════════════════════════════════════════════════════════════

class SmokeRoleGuardsTest(_SmokeBase):
    """
    Проверяем, что никто не видит чужого:
    - объекты другого пользователя возвращают 404
    - чужие разделы недоступны
    """

    def test_advertiser_cannot_see_panel(self):
        c = Client()
        c.force_login(self.adv)
        resp = c.get(reverse("web:admin_dashboard"))
        self.assertNotEqual(resp.status_code, 200)

    def test_blogger_cannot_see_panel(self):
        c = Client()
        c.force_login(self.blg)
        resp = c.get(reverse("web:admin_dashboard"))
        self.assertNotEqual(resp.status_code, 200)

    def test_blogger_cannot_create_campaign(self):
        c = Client()
        c.force_login(self.blg)
        resp = c.get(reverse("web:campaign_create"))
        self.assertNotEqual(resp.status_code, 200)

    def test_advertiser_cannot_see_bloggers_deal(self):
        """
        Создаём вторую сделку с другим рекламодателем.
        Первый рекламодатель не должен видеть её.
        """
        adv2 = _make_user("adv2@smoke.com", User.Role.ADVERTISER)
        _make_wallet(adv2, 100_000)
        deal2 = _make_deal(adv2, self.blg, self.campaign, self.platform, amount=50_000)

        c = Client()
        c.force_login(self.adv)
        resp = c.get(reverse("web:deal_detail", kwargs={"pk": deal2.pk}))
        self.assertEqual(resp.status_code, 404)

    def test_blogger_cannot_see_advertisers_campaign_detail_if_not_active(self):
        """Блогер не видит чужую DRAFT/MODERATION кампанию."""
        draft = _make_campaign(self.adv, status=Campaign.Status.DRAFT)
        c = Client()
        c.force_login(self.blg)
        resp = c.get(reverse("web:campaign_detail", kwargs={"pk": draft.pk}))
        self.assertEqual(resp.status_code, 404)

    def test_anonymous_deals_redirect(self):
        resp = self.client.get(reverse("web:deal_list"))
        self.assertEqual(resp.status_code, 302)

    def test_anonymous_campaigns_redirect(self):
        resp = self.client.get(reverse("web:campaign_list"))
        self.assertEqual(resp.status_code, 302)

    def test_anonymous_panel_redirect(self):
        resp = self.client.get(reverse("web:admin_dashboard"))
        self.assertEqual(resp.status_code, 302)

    def test_anonymous_profile_redirect(self):
        resp = self.client.get(reverse("web:profile"))
        self.assertEqual(resp.status_code, 302)

    def test_staff_can_see_any_campaign(self):
        """Регрессионный тест на баг campaign_detail под staff."""
        c = Client()
        c.force_login(self.staff)
        resp = c.get(reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk}))
        self.assertEqual(resp.status_code, 200)

    def test_staff_can_see_any_deal(self):
        """Staff видит любую сделку."""
        c = Client()
        c.force_login(self.staff)
        resp = c.get(reverse("web:deal_detail", kwargs={"pk": self.deal.pk}))
        self.assertEqual(resp.status_code, 200)
