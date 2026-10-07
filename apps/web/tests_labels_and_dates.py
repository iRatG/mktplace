"""
QA camp_test_3 (07.10.2026): русские подписи вместо «Draft»/«Fixed»/«Click» (скриншоты 3, 7), даты кампании словами
(2.1), очередь модерации без «Одобрить» и с окном контента (3.2, скриншот 8), подписчики с разрядами (скриншот 10),
периоды в финансах (11.2, 11.3).
"""
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.campaigns.models import Campaign
from apps.deals.models import Deal
from apps.platforms.models import Platform
from apps.users.models import User
from apps.web.templatetags.labels import content_type_label, long_date, social_label

TEMPLATES_DIR = Path(settings.BASE_DIR) / "templates"


def _user(email, role=User.Role.ADVERTISER, is_staff=False):
    return User.objects.create_user(
        email=email, password="pass1234", role=role, status=User.Status.ACTIVE, is_staff=is_staff,
    )


def _campaign(advertiser, status=Campaign.Status.DRAFT):
    return Campaign.objects.create(
        advertiser=advertiser, name="Цветочный магазин", payment_type=Campaign.PaymentType.FIXED,
        fixed_price=Decimal("150000"), budget=Decimal("300000"), status=status,
        start_date=date(2031, 10, 8), end_date=date(2031, 11, 8),
        content_start=date(2031, 10, 8), deadline=date(2031, 11, 2),
        content_types=["post", "stories"], allowed_socials=["instagram", "vk"],
    )


class FiltersTest(SimpleTestCase):
    def test_long_date(self):
        self.assertEqual(long_date(date(2026, 10, 8)), "08 октября 2026")
        self.assertEqual(long_date(None), "")

    def test_codes_to_labels(self):
        self.assertEqual(content_type_label("stories"), "Сторис")
        self.assertEqual(social_label("vk"), "ВКонтакте")
        self.assertEqual(content_type_label("unknown"), "unknown")

    def test_choice_labels_are_russian(self):
        self.assertEqual(Campaign.Status.DRAFT.label, "Черновик")
        self.assertEqual(Campaign.PaymentType.FIXED.label, "Фиксированная")
        self.assertEqual(Campaign.CPAType.CLICK.label, "Клик")
        self.assertEqual(Deal.Status.WAITING_PUBLICATION.label, "Ждёт публикации")
        self.assertEqual(Platform.Status.APPROVED.label, "Одобрена")


class TemplatesGuardTest(SimpleTestCase):
    """Страж: даты кампании — словами (long_date), подписчики — с разрядами (money)."""

    def _templates(self):
        return sorted(TEMPLATES_DIR.rglob("*.html"))

    def test_campaign_dates_use_long_date(self):
        pattern = re.compile(r"campaign\.(start_date|end_date|content_start|deadline)\|date:")
        offenders = [
            f"{p.relative_to(TEMPLATES_DIR)}: {m.group(0)}"
            for p in self._templates() for m in pattern.finditer(p.read_text(encoding="utf-8"))
        ]
        self.assertEqual(offenders, [], "Даты кампании — через |long_date")

    def test_subscribers_use_money(self):
        pattern = re.compile(r"\{\{[^}]*\b(?<!form\.)\w+\.subscribers\b(?![^}]*\|money)[^}]*\}\}")
        offenders = [
            f"{p.relative_to(TEMPLATES_DIR)}: {m.group(0)}"
            for p in self._templates() for m in pattern.finditer(p.read_text(encoding="utf-8"))
            if "form." not in m.group(0)
        ]
        self.assertEqual(offenders, [], "Подписчики — через |money")

    def test_filters_are_loaded(self):
        """Шаблон с |money или фильтрами labels подключает библиотеку (иначе страница падает с 500)."""
        needs = {"money": r"\|money\b", "labels": r"\|(long_date|content_type_label|social_label)\b"}
        offenders = []
        for p in self._templates():
            text = p.read_text(encoding="utf-8")
            loads = " ".join(re.findall(r"\{% load ([^%]+)%\}", text)).split()
            for lib, pattern in needs.items():
                if re.search(pattern, text) and lib not in loads:
                    offenders.append(f"{p.relative_to(TEMPLATES_DIR)}: нет {{% load {lib} %}}")
        self.assertEqual(offenders, [])


class PagesTest(TestCase):
    def setUp(self):
        self.adv = _user("adv@test.com")
        self.staff = _user("staff@test.com", is_staff=True)

    def test_owner_sees_russian_labels_and_long_dates(self):
        campaign = _campaign(self.adv)
        self.client.force_login(self.adv)
        html = self.client.get(reverse("web:campaign_detail", kwargs={"pk": campaign.pk})).content.decode()
        for text in ("Черновик", "Фиксированная", "Пост", "Сторис", "ВКонтакте", "08 октября 2031", "02 ноября 2031"):
            self.assertIn(text, html)
        for text in (">Draft<", ">Fixed<", "08.10.2031"):
            self.assertNotIn(text, html)

    def test_campaign_list_status_russian(self):
        _campaign(self.adv, status=Campaign.Status.ACTIVE)
        self.client.force_login(self.adv)
        html = self.client.get(reverse("web:campaign_list")).content.decode()
        self.assertIn("Активна", html)
        self.assertNotIn("Active", html)

    def test_moderation_queue_has_no_approve_and_shows_window(self):
        campaign = _campaign(self.adv, status=Campaign.Status.MODERATION)
        self.client.force_login(self.staff)
        html = self.client.get(reverse("web:admin_campaigns")).content.decode()
        self.assertNotIn(reverse("web:admin_campaign_approve", kwargs={"pk": campaign.pk}), html)
        self.assertNotIn(reverse("web:admin_campaign_reject", kwargs={"pk": campaign.pk}), html)
        self.assertIn(reverse("web:admin_campaign_detail", kwargs={"pk": campaign.pk}), html)
        self.assertIn("Приём контента: с 08 октября 2031 до 02 ноября 2031", html)
        self.assertNotIn("дедлайн", html)

    def test_moderation_card_keeps_actions_and_long_dates(self):
        campaign = _campaign(self.adv, status=Campaign.Status.MODERATION)
        self.client.force_login(self.staff)
        html = self.client.get(reverse("web:admin_campaign_detail", kwargs={"pk": campaign.pk})).content.decode()
        self.assertIn(reverse("web:admin_campaign_approve", kwargs={"pk": campaign.pk}), html)
        self.assertIn("08 октября 2031", html)
        self.assertIn("Фиксированная", html)

    def test_finance_periods_labelled(self):
        self.client.force_login(self.staff)
        html = self.client.get(reverse("web:admin_dashboard")).content.decode()
        self.assertIn("Доход платформы · за всё время", html)
        self.assertIn("Оборот сделок · за последние 30 дней", html)
        self.assertIn("Комиссия со всех оплат (сделки и CPA)", html)

    def test_deal_subscribers_with_separators(self):
        blogger = _user("bl@test.com", User.Role.BLOGGER)
        platform = Platform.objects.create(
            blogger=blogger, social_type=Platform.SocialType.INSTAGRAM, url="https://instagram.com/b",
            subscribers=12000, price_post=Decimal("50000"), status=Platform.Status.APPROVED,
        )
        deal = Deal.objects.create(
            campaign=_campaign(self.adv, status=Campaign.Status.ACTIVE), blogger=blogger, advertiser=self.adv,
            platform=platform, amount=Decimal("150000"), status=Deal.Status.IN_PROGRESS,
        )
        self.client.force_login(self.adv)
        html = self.client.get(reverse("web:deal_detail", kwargs={"pk": deal.pk})).content.decode()
        self.assertIn("12 000 подп.", html)
