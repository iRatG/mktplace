"""
QA camp_test_3 (07.10.2026), шаг 7.1: комментарий к отклонённому отклику был виден только в уведомлениях.
Теперь — в «Требует действия» на дашборде блогера и меткой в каталоге.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.campaigns.models import Campaign
from apps.campaigns.models import Response as CampaignResponse
from apps.platforms.models import Platform
from apps.users.models import User

R = CampaignResponse.Status


def _user(email, role):
    return User.objects.create_user(email=email, password="pass1234", role=role, status=User.Status.ACTIVE)


class BloggerRejectionsTest(TestCase):
    def setUp(self):
        self.adv = _user("adv@test.com", User.Role.ADVERTISER)
        self.blogger = _user("bl@test.com", User.Role.BLOGGER)
        self.platform = Platform.objects.create(
            blogger=self.blogger, social_type=Platform.SocialType.INSTAGRAM, url="https://instagram.com/b",
            subscribers=12000, price_post=Decimal("50000"), status=Platform.Status.APPROVED,
        )
        self.campaign = self._campaign("Цветочный магазин")
        self.client.force_login(self.blogger)

    def _campaign(self, name, status=Campaign.Status.ACTIVE, **extra):
        return Campaign.objects.create(
            advertiser=self.adv, name=name, payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=status, **extra,
        )

    def _response(self, campaign, status, reason=""):
        return CampaignResponse.objects.create(
            campaign=campaign, blogger=self.blogger, platform=self.platform, content_type="post",
            proposed_price=Decimal("400000"), status=status, rejection_reason=reason,
        )

    def _dashboard(self):
        return self.client.get(reverse("web:blogger_dashboard")).content.decode()

    def test_rejected_with_comment_on_dashboard(self):
        self._response(self.campaign, R.REJECTED, "Готовы на 150 000")
        html = self._dashboard()
        self.assertIn("Требует действия", html)
        self.assertIn("Готовы на 150 000", html)
        self.assertIn("Откликнуться снова", html)
        self.assertIn(reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk}), html)

    def test_not_shown_after_new_response(self):
        self._response(self.campaign, R.REJECTED, "Готовы на 150 000")
        self._response(self.campaign, R.PENDING)
        self.assertNotIn("Готовы на 150 000", self._dashboard())

    def test_not_shown_for_inactive_or_expired_campaign(self):
        paused = self._campaign("Пауза", status=Campaign.Status.PAUSED)
        expired = self._campaign("Прошла", end_date=timezone.localdate() - timedelta(days=1))
        self._response(paused, R.REJECTED, "Комментарий паузы")
        self._response(expired, R.REJECTED, "Комментарий прошедшей")
        html = self._dashboard()
        self.assertNotIn("Комментарий паузы", html)
        self.assertNotIn("Комментарий прошедшей", html)

    def test_only_latest_rejection_per_campaign(self):
        self._response(self.campaign, R.REJECTED, "Первый комментарий")
        self._response(self.campaign, R.REJECTED, "Второй комментарий")
        html = self._dashboard()
        self.assertEqual(html.count("Откликнуться снова"), 1)

    def test_catalog_marks(self):
        rejected = self.campaign
        pending = self._campaign("Ждёт")
        self._response(rejected, R.REJECTED, "Дорого")
        self._response(pending, R.PENDING)
        self._campaign("Без отклика")
        html = self.client.get(reverse("web:catalog")).content.decode()
        self.assertEqual(html.count("Ваш отклик отклонён"), 1)
        self.assertEqual(html.count("Ваш отклик ждёт решения"), 1)

    def test_catalog_queries_do_not_grow_per_campaign(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        url = reverse("web:catalog")

        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(url)
            return len(ctx.captured_queries)

        self._response(self.campaign, R.REJECTED, "x")
        few = count()
        for i in range(5):
            self._response(self._campaign(f"К{i}"), R.REJECTED, "x")
        self.assertEqual(count(), few)
