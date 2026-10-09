"""
Решение бизнеса 06.10.2026 (опросник по camp_test_2, вопрос 4 — Б): окно приёма контента «с … по …»;
конец окна не позже чем за 5 рабочих дней (пн–пт) до окончания кампании.
Решение бизнеса 07.10.2026 (BZ-1, #29): окно может целиком лежать до старта кампании.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.campaigns.testing import CARD_FIELDS
from apps.campaigns.models import Campaign
from apps.campaigns.validation import campaign_param_errors, latest_content_end, working_days_before
from apps.users.models import User
from apps.web.campaign_proposals import form_data_from_campaign


class WorkingDaysTest(SimpleTestCase):
    def test_skips_weekends(self):
        # пятница 30.10.2026 − 5 рабочих дней = пятница 23.10.2026
        self.assertEqual(working_days_before(date(2026, 10, 30), 5), date(2026, 10, 23))
        # понедельник 02.11.2026 − 1 рабочий день = пятница 30.10.2026
        self.assertEqual(working_days_before(date(2026, 11, 2), 1), date(2026, 10, 30))
        # воскресенье 01.11.2026 − 5 = понедельник 26.10.2026
        self.assertEqual(working_days_before(date(2026, 11, 1), 5), date(2026, 10, 26))

    def test_latest_content_end(self):
        self.assertEqual(latest_content_end(date(2026, 10, 30)), date(2026, 10, 23))


class ContentWindowRulesTest(SimpleTestCase):
    def _errors(self, **overrides):
        values = dict(
            payment_type=Campaign.PaymentType.FIXED, fixed_price=Decimal("150000"), budget=Decimal("1500000"),
            start_date=date(2026, 10, 1), end_date=date(2026, 10, 30),
            content_start=date(2026, 10, 5), deadline=date(2026, 10, 23), max_bloggers=0,
        )
        values.update(overrides)
        return campaign_param_errors(**values)

    def test_valid_window(self):
        self.assertEqual(self._errors(), {})

    def test_end_too_close_to_campaign_end(self):
        errors = self._errors(deadline=date(2026, 10, 26))
        self.assertIn("5 рабочих дней", errors["deadline"])
        self.assertIn("23.10.2026", errors["deadline"])

    def test_start_after_end(self):
        self.assertIn("content_start", self._errors(content_start=date(2026, 10, 24), deadline=date(2026, 10, 23)))

    def test_window_entirely_before_campaign_start(self):
        self.assertEqual(self._errors(content_start=date(2026, 9, 20), deadline=date(2026, 9, 30)), {})
        self.assertEqual(self._errors(content_start=None, deadline=date(2026, 9, 30)), {})

    def test_window_may_start_before_campaign(self):
        self.assertEqual(self._errors(content_start=date(2026, 9, 25)), {})


def _user(email, role=User.Role.ADVERTISER):
    return User.objects.create_user(email=email, password="pass1234", role=role, status=User.Status.ACTIVE)


class ContentWindowFormTest(TestCase):
    def setUp(self):
        self.adv = _user("adv@test.com")
        self.client.force_login(self.adv)
        self.today = timezone.now().date()

    def _data(self, **overrides):
        data = {
            "name": "Кампания", "payment_type": "fixed", "fixed_price": "150000", "budget": "1500000",
            "start_date": (self.today + timedelta(days=1)).isoformat(),
            "end_date": (self.today + timedelta(days=40)).isoformat(),
            "content_start": (self.today + timedelta(days=1)).isoformat(),
            "deadline": (self.today + timedelta(days=20)).isoformat(),
            "min_subscribers": "0", "max_bloggers": "0",
        }
        data.update(overrides)
        return data

    def test_create_with_window_and_shown(self):
        self.client.post(reverse("web:campaign_create"), self._data())
        campaign = Campaign.objects.get()
        self.assertEqual(campaign.content_start, self.today + timedelta(days=1))
        page = self.client.get(reverse("web:campaign_detail", kwargs={"pk": campaign.pk}))
        self.assertContains(page, "Приём контента")

    def test_window_end_too_late_shows_error_at_field(self):
        end = self.today + timedelta(days=40)
        r = self.client.post(reverse("web:campaign_create"), self._data(deadline=(end - timedelta(days=1)).isoformat()))
        self.assertIn("deadline", r.context["form"].errors)
        self.assertFalse(Campaign.objects.exists())

    def test_window_entirely_before_start_saved(self):
        self.client.post(reverse("web:campaign_create"), self._data(
            start_date=(self.today + timedelta(days=15)).isoformat(),
            content_start=(self.today + timedelta(days=1)).isoformat(),
            deadline=(self.today + timedelta(days=10)).isoformat(),
        ))
        campaign = Campaign.objects.get()
        self.assertEqual(campaign.deadline, self.today + timedelta(days=10))
        self.assertLess(campaign.deadline, campaign.start_date)

    def test_form_hint_allows_window_before_start(self):
        page = self.client.get(reverse("web:campaign_create"))
        self.assertContains(page, "Приём контента может пройти и до старта кампании")
        self.assertNotContains(page, "не раньше начала кампании")

    def test_content_start_in_past_rejected_on_create(self):
        r = self.client.post(reverse("web:campaign_create"),
                             self._data(content_start=(self.today - timedelta(days=1)).isoformat()))
        self.assertIn("content_start", r.context["form"].errors)

    def test_paused_campaign_with_started_window_can_be_edited(self):
        campaign = Campaign.objects.create(
            **CARD_FIELDS,
            advertiser=self.adv, name="Идёт", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=Campaign.Status.PAUSED,
            start_date=self.today - timedelta(days=5), end_date=self.today + timedelta(days=40),
            content_start=self.today - timedelta(days=5), deadline=self.today + timedelta(days=20),
        )
        data = form_data_from_campaign(campaign)
        payload = {k: data.getlist(k) for k in data}
        payload["description"] = "Обновили описание"
        self.client.post(reverse("web:campaign_edit", kwargs={"pk": campaign.pk}), payload)
        campaign.refresh_from_db()
        self.assertEqual(campaign.description, "Обновили описание")
        self.assertEqual(campaign.status, Campaign.Status.MODERATION)

    def test_api_same_rule(self):
        api = APIClient()
        api.force_authenticate(self.adv)
        end = self.today + timedelta(days=40)
        r = api.post(reverse("campaigns:campaign-list"), {
            "name": "API", "payment_type": "fixed", "fixed_price": "150000", "budget": "1500000",
            "start_date": (self.today + timedelta(days=1)).isoformat(), "end_date": end.isoformat(),
            "content_start": (self.today + timedelta(days=1)).isoformat(),
            "deadline": (end - timedelta(days=1)).isoformat(),
        }, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("deadline", r.json())

    def test_api_window_entirely_before_start_saved(self):
        api = APIClient()
        api.force_authenticate(self.adv)
        r = api.post(reverse("campaigns:campaign-list"), {
            "name": "API", "payment_type": "fixed", "fixed_price": "150000", "budget": "1500000",
            "start_date": (self.today + timedelta(days=15)).isoformat(),
            "end_date": (self.today + timedelta(days=40)).isoformat(),
            "content_start": (self.today + timedelta(days=1)).isoformat(),
            "deadline": (self.today + timedelta(days=10)).isoformat(),
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Campaign.objects.get().deadline, self.today + timedelta(days=10))
