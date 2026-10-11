"""
#51: отклик ждёт ответа до конца дня «окончание приёма контента − 2 рабочих дня»; после этого дня отклики не
принимаются (сайт и API); правка дат пересчитывает срок ожидающих; миграция переносит ожидающие на новое правило.
"""
import importlib
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.apps import apps as django_apps
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.campaigns.models import Campaign
from apps.campaigns.models import Response as CampaignResponse
from apps.campaigns.validation import (
    refresh_response_deadlines, response_deadline_for, response_last_day, responses_closed_error,
    working_days_before,
)
from apps.platforms.models import Platform
from apps.users.models import User


def _end_of(day):
    return timezone.make_aware(datetime.combine(day, time(23, 59, 59)))


class FormulaTest(TestCase):
    def test_example_from_business(self):
        """Кампания 10.10–10.11.2026, приём контента до 03.11 (вт) → заявки до 30.10 (пт)."""
        campaign = Campaign(start_date=date(2026, 10, 10), end_date=date(2026, 11, 10), deadline=date(2026, 11, 3))
        self.assertEqual(response_last_day(campaign), date(2026, 10, 30))
        self.assertEqual(response_deadline_for(campaign), _end_of(date(2026, 10, 30)))

    def test_without_content_window_uses_latest_allowed(self):
        campaign = Campaign(start_date=date(2026, 10, 10), end_date=date(2026, 11, 10))
        self.assertEqual(response_last_day(campaign), date(2026, 10, 30))

    def test_without_dates_keeps_seven_days(self):
        before = timezone.now()
        deadline = response_deadline_for(Campaign())
        self.assertAlmostEqual((deadline - before).total_seconds(), timedelta(days=7).total_seconds(), delta=5)

    def test_monday_content_end_skips_weekend(self):
        campaign = Campaign(end_date=date(2026, 11, 30), deadline=date(2026, 11, 16))  # понедельник
        self.assertEqual(response_last_day(campaign), date(2026, 11, 12))  # четверг


class ResponseDeadlineTest(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.adv = User.objects.create_user(email="adv@test.com", password="pass1234", role=User.Role.ADVERTISER,
                                            status=User.Status.ACTIVE)
        self.blogger = User.objects.create_user(email="bl@test.com", password="pass1234", role=User.Role.BLOGGER,
                                                status=User.Status.ACTIVE)
        self.platform = Platform.objects.create(
            blogger=self.blogger, social_type=Platform.SocialType.INSTAGRAM, url="https://instagram.com/dl",
            subscribers=12000, price_post=Decimal("50000"), status=Platform.Status.APPROVED,
        )
        self.campaign = Campaign.objects.create(
            advertiser=self.adv, name="Срок", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=Campaign.Status.ACTIVE,
            start_date=self.today, end_date=self.today + timedelta(days=40), deadline=self.today + timedelta(days=21),
        )

    def _respond_web(self):
        self.client.force_login(self.blogger)
        return self.client.post(reverse("web:campaign_respond", kwargs={"pk": self.campaign.pk}),
                                {"platform": self.platform.pk, "content_type": "post"}, follow=True)

    def _respond_api(self):
        api = APIClient()
        api.force_authenticate(self.blogger)
        return api.post(reverse("campaigns:response-list"),
                        {"campaign": self.campaign.pk, "platform": self.platform.pk, "content_type": "post"},
                        format="json")

    def _close(self):
        """Окончание приёма контента — сегодня: последний день заявок был 2 рабочих дня назад."""
        Campaign.objects.filter(pk=self.campaign.pk).update(deadline=self.today)
        self.campaign.refresh_from_db()

    def test_web_response_gets_deadline_and_page_shows_it(self):
        self._respond_web()
        resp = CampaignResponse.objects.get()
        last = working_days_before(self.campaign.deadline, 2)
        self.assertEqual(resp.expires_at, _end_of(last))
        CampaignResponse.objects.all().delete()
        page = self.client.get(reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk}))
        self.assertContains(page, f"Заявки принимаются до {last:%d.%m.%Y}")

    def test_api_response_gets_deadline(self):
        self.assertEqual(self._respond_api().status_code, 201)
        self.assertEqual(CampaignResponse.objects.get().expires_at, response_deadline_for(self.campaign))

    def test_closed_web(self):
        self._close()
        page = self._respond_web()
        self.assertFalse(CampaignResponse.objects.exists())
        last = working_days_before(self.today, 2)
        self.assertContains(page, f"Приём заявок по кампании закончился {last:%d.%m.%Y}.")
        self.assertNotContains(page, "Отправить отклик")

    def test_closed_api(self):
        self._close()
        answer = self._respond_api()
        self.assertEqual(answer.status_code, 400)
        self.assertIn("Приём заявок по кампании закончился", answer.content.decode())
        self.assertFalse(CampaignResponse.objects.exists())

    def test_campaign_without_dates_never_closed(self):
        Campaign.objects.filter(pk=self.campaign.pk).update(deadline=None, end_date=None)
        self.campaign.refresh_from_db()
        self.assertIsNone(responses_closed_error(self.campaign))

    def test_edit_dates_refreshes_pending(self):
        self._respond_web()
        resp = CampaignResponse.objects.get()
        CampaignResponse.objects.filter(pk=resp.pk).update(reminder_sent_at=timezone.now())
        later = self.today + timedelta(days=28)
        Campaign.objects.filter(pk=self.campaign.pk).update(deadline=later)
        self.campaign.refresh_from_db()
        self.assertEqual(refresh_response_deadlines(self.campaign), 1)
        resp.refresh_from_db()
        self.assertEqual(resp.expires_at, _end_of(working_days_before(later, 2)))
        self.assertIsNone(resp.reminder_sent_at)  # срок сдвинулся позже — напоминание отправится заново
        self.assertEqual(refresh_response_deadlines(self.campaign), 0)

    def test_web_edit_calls_refresh(self):
        self._respond_web()
        Campaign.objects.filter(pk=self.campaign.pk).update(status=Campaign.Status.DRAFT)
        from apps.campaigns.testing import CARD_FIELDS

        later = self.today + timedelta(days=28)
        self.client.force_login(self.adv)
        data = {
            "name": "Срок", "payment_type": "fixed", "fixed_price": "150000", "budget": "1500000",
            "start_date": self.today.isoformat(), "end_date": (self.today + timedelta(days=40)).isoformat(),
            "deadline": later.isoformat(), "max_bloggers": "0", "min_subscribers": "0", "visibility": "open",
            "content_lead_days": "5", "review_days": "2", "content_units": "1", "min_retention_days": "3",
            "content_rights": "blogger", **CARD_FIELDS,
        }
        answer = self.client.post(reverse("web:campaign_edit", kwargs={"pk": self.campaign.pk}), data)
        self.assertEqual(answer.status_code, 302, answer.content.decode()[:3000])
        self.assertEqual(CampaignResponse.objects.get().expires_at, _end_of(working_days_before(later, 2)))

    def test_migration_moves_pending_to_new_rule(self):
        self._respond_web()
        CampaignResponse.objects.update(expires_at=timezone.now() + timedelta(days=7))
        migration = importlib.import_module("apps.campaigns.migrations.0019_response_deadline_by_content_window")
        migration.forwards(django_apps, None)
        self.assertEqual(CampaignResponse.objects.get().expires_at, response_deadline_for(self.campaign))

    def test_retry_list_skips_closed_campaigns(self):
        from apps.web.views.pages import rejected_responses_to_retry

        CampaignResponse.objects.create(
            campaign=self.campaign, blogger=self.blogger, platform=self.platform, content_type="post",
            status=CampaignResponse.Status.REJECTED, rejection_reason="Дорого",
        )
        self.assertEqual(len(rejected_responses_to_retry(self.blogger)), 1)
        self._close()
        self.assertEqual(rejected_responses_to_retry(self.blogger), [])
