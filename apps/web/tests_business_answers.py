"""
Решения бизнеса по кампаниям (04.10.2026, GitHub issue #5, openspec change
apply-business-answers-campaigns):
  - правка приостановленной кампании → повторная модерация (веб и API);
  - минимальная цена за размещение (веб и API);
  - лимит блогеров не ниже числа занятых мест;
  - стартовый список из 17 категорий.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.campaigns.testing import CARD_FIELDS
from apps.campaigns.models import Campaign
from apps.campaigns.validation import campaign_param_errors, deals_in_cap
from apps.deals.models import Deal
from apps.platforms.models import Category, Platform

User = get_user_model()
FIXED = Campaign.PaymentType.FIXED

BUSINESS_CATEGORY_NAMES = [
    "Lifestyle & ЗОЖ", "Технологии & IT", "Красота и уход", "Мода и одежда", "Еда и рестораны",
    "Путешествия", "Спорт и фитнес", "Авто", "Финансы и бизнес", "Образование", "Дети и семья",
    "Игры и киберспорт", "Юмор и развлечения", "Музыка и кино", "Дом и недвижимость",
    "Электроника и онлайн-покупки", "Другое",
]


def _user(email, role):
    user = User.objects.create_user(email=email, password="pass1234", role=role)
    user.status = User.Status.ACTIVE
    user.is_email_confirmed = True
    user.save(update_fields=["status", "is_email_confirmed"])
    return user


def _form_data(**overrides):
    today = timezone.now().date()
    data = {
        "name": "Кампания",
        "payment_type": "fixed",
        "fixed_price": "150000",
        "budget": "1500000",
        "start_date": (today + timedelta(days=1)).isoformat(),
        "end_date": (today + timedelta(days=30)).isoformat(),
        "deadline": (today + timedelta(days=20)).isoformat(),
        "min_subscribers": "0",
        "max_bloggers": "0",
        **CARD_FIELDS,
    }
    data.update(overrides)
    return data


def _campaign(advertiser, status, **extra):
    fields = dict(
        advertiser=advertiser, name="Кампания", payment_type=FIXED,
        fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=status,
        deadline=timezone.now().date() + timedelta(days=20),
        # Даты обязательны для модерации (Р4, #33).
        start_date=timezone.now().date(), end_date=timezone.now().date() + timedelta(days=40),
    )
    fields.update(CARD_FIELDS)  # карточка заполнена — кампанию можно отправить на модерацию (#42)
    fields.update(extra)
    return Campaign.objects.create(**fields)


_counter = 0


def _deal(campaign, status):
    global _counter
    _counter += 1
    blogger = _user(f"ba_blogger{_counter}@test.com", User.Role.BLOGGER)
    platform = Platform.objects.create(
        blogger=blogger, social_type=Platform.SocialType.INSTAGRAM,
        url=f"https://instagram.com/ba{_counter}", subscribers=5000,
        status=Platform.Status.APPROVED,
    )
    return Deal.objects.create(
        campaign=campaign, blogger=blogger, platform=platform,
        advertiser=campaign.advertiser, amount=Decimal("150000"), status=status,
    )


class EditPausedCampaignTest(TestCase):
    def setUp(self):
        self.adv = _user("ba_adv@test.com", User.Role.ADVERTISER)
        self.client.force_login(self.adv)

    def test_editing_paused_sends_to_moderation(self):
        c = _campaign(self.adv, Campaign.Status.PAUSED)
        r = self.client.post(reverse("web:campaign_edit", args=[c.pk]), _form_data(name="Новое имя"))
        self.assertRedirects(r, reverse("web:campaign_detail", args=[c.pk]))
        c.refresh_from_db()
        self.assertEqual(c.name, "Новое имя")
        self.assertEqual(c.status, Campaign.Status.MODERATION)

    def test_invalid_edit_keeps_paused(self):
        c = _campaign(self.adv, Campaign.Status.PAUSED)
        r = self.client.post(reverse("web:campaign_edit", args=[c.pk]), _form_data(fixed_price="5000"))
        self.assertEqual(r.status_code, 200)
        c.refresh_from_db()
        self.assertEqual(c.status, Campaign.Status.PAUSED)

    def test_active_moderation_completed_not_editable(self):
        for status in (Campaign.Status.ACTIVE, Campaign.Status.MODERATION, Campaign.Status.COMPLETED):
            c = _campaign(self.adv, status)
            self.client.post(reverse("web:campaign_edit", args=[c.pk]), _form_data(name="Нельзя"))
            c.refresh_from_db()
            self.assertEqual(c.status, status)
            self.assertNotEqual(c.name, "Нельзя")

    def test_resume_without_edit_goes_active(self):
        c = _campaign(self.adv, Campaign.Status.PAUSED)
        self.client.post(reverse("web:campaign_resume", args=[c.pk]))
        c.refresh_from_db()
        self.assertEqual(c.status, Campaign.Status.ACTIVE)

    def test_detail_shows_edit_for_paused(self):
        c = _campaign(self.adv, Campaign.Status.PAUSED)
        r = self.client.get(reverse("web:campaign_detail", args=[c.pk]))
        self.assertContains(r, reverse("web:campaign_edit", args=[c.pk]))
        self.assertContains(r, "повторную модерацию")

    def test_api_editing_paused_sends_to_moderation(self):
        c = _campaign(self.adv, Campaign.Status.PAUSED)
        api = APIClient()
        api.force_authenticate(self.adv)
        r = api.patch(f"/api/v1/campaigns/{c.pk}/", {"name": "API имя"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        c.refresh_from_db()
        self.assertEqual(c.status, Campaign.Status.MODERATION)

    def test_api_active_not_editable(self):
        c = _campaign(self.adv, Campaign.Status.ACTIVE)
        api = APIClient()
        api.force_authenticate(self.adv)
        r = api.patch(f"/api/v1/campaigns/{c.pk}/", {"name": "API имя"}, format="json")
        self.assertEqual(r.status_code, 403)


class MinFixedPriceTest(TestCase):
    def setUp(self):
        self.adv = _user("ba_min@test.com", User.Role.ADVERTISER)
        self.client.force_login(self.adv)

    def test_below_min_rejected(self):
        r = self.client.post(reverse("web:campaign_create"), _form_data(fixed_price="9 999", budget="100 000"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Минимальная цена за размещение — 10 000")
        self.assertFalse(Campaign.objects.filter(advertiser=self.adv).exists())

    def test_exact_min_ok(self):
        self.client.post(reverse("web:campaign_create"), _form_data(fixed_price="10 000", budget="100 000"))
        self.assertTrue(Campaign.objects.filter(advertiser=self.adv, fixed_price=10000).exists())

    def test_form_shows_min_hint(self):
        r = self.client.get(reverse("web:campaign_create"))
        self.assertContains(r, "Минимум — 10 000")

    @override_settings(CAMPAIGN_MIN_FIXED_PRICE=50000)
    def test_min_comes_from_settings(self):
        errors = campaign_param_errors(
            payment_type=FIXED, fixed_price=Decimal("40000"), budget=Decimal("1000000"),
            start_date=None, end_date=None, deadline=None, max_bloggers=0,
        )
        self.assertIn("50 000", errors["fixed_price"])

    def test_api_below_min_400(self):
        api = APIClient()
        api.force_authenticate(self.adv)
        r = api.post("/api/v1/campaigns/", {
            "name": "API", "payment_type": "fixed", "fixed_price": "9999", "budget": "100000",
        }, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("fixed_price", r.json())


class MaxBloggersVsTakenSlotsTest(TestCase):
    def setUp(self):
        self.adv = _user("ba_cap@test.com", User.Role.ADVERTISER)
        self.client.force_login(self.adv)
        self.c = _campaign(self.adv, Campaign.Status.PAUSED, max_bloggers=5)
        for status in (Deal.Status.IN_PROGRESS, Deal.Status.CHECKING, Deal.Status.COMPLETED):
            _deal(self.c, status)
        _deal(self.c, Deal.Status.CANCELLED)  # не занимает место

    def test_deals_in_cap_counts_completed_not_cancelled(self):
        self.assertEqual(deals_in_cap(self.c), 3)

    def test_lowering_below_taken_rejected(self):
        r = self.client.post(reverse("web:campaign_edit", args=[self.c.pk]), _form_data(max_bloggers="2"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "уже занято мест: 3")
        self.c.refresh_from_db()
        self.assertEqual(self.c.max_bloggers, 5)
        self.assertEqual(self.c.status, Campaign.Status.PAUSED)

    def test_zero_means_no_limit(self):
        self.client.post(reverse("web:campaign_edit", args=[self.c.pk]), _form_data(max_bloggers="0"))
        self.c.refresh_from_db()
        self.assertEqual(self.c.max_bloggers, 0)
        self.assertEqual(self.c.status, Campaign.Status.MODERATION)

    def test_equal_to_taken_ok(self):
        self.client.post(reverse("web:campaign_edit", args=[self.c.pk]), _form_data(max_bloggers="3"))
        self.c.refresh_from_db()
        self.assertEqual(self.c.max_bloggers, 3)


class BusinessCategoriesTest(TestCase):
    def test_all_17_categories_present(self):
        names = set(Category.objects.values_list("name", flat=True))
        for name in BUSINESS_CATEGORY_NAMES:
            self.assertIn(name, names)

    def test_existing_slugs_kept(self):
        self.assertEqual(Category.objects.get(slug="lifestyle").name, "Lifestyle & ЗОЖ")
        self.assertEqual(Category.objects.get(slug="tech").name, "Технологии & IT")

    def test_none_regulated(self):
        self.assertFalse(Category.objects.filter(name__in=BUSINESS_CATEGORY_NAMES, is_regulated=True).exists())

    def test_loader_is_idempotent(self):
        from importlib import import_module

        from django.apps import apps as django_apps

        migration = import_module("apps.platforms.migrations.0004_business_categories")
        before = Category.objects.count()
        migration.load_business_categories(django_apps, None)
        self.assertEqual(Category.objects.count(), before)

    def test_campaign_form_lists_categories(self):
        adv = _user("ba_cat@test.com", User.Role.ADVERTISER)
        self.client.force_login(adv)
        r = self.client.get(reverse("web:campaign_create"))
        for name in ("Красота и уход", "Электроника и онлайн-покупки", "Другое"):
            self.assertContains(r, name)
