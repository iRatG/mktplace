"""
Форма кампании и модерация: согласованность параметров (веб и API), ввод сумм
с пробелами, CPA через веб, повторная отправка отклонённой кампании,
обязательная причина отклонения, карточка кампании для модерации.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.campaigns.models import Campaign
from apps.campaigns.validation import campaign_param_errors
from apps.notifications.models import Notification
from apps.web.forms import CampaignForm
from apps.web.templatetags.money import money

User = get_user_model()

FIXED = Campaign.PaymentType.FIXED


def _user(email, role, **extra):
    return User.objects.create_user(
        email=email, password="pass1234", role=role, is_active=True, **extra
    )


def _today():
    return timezone.now().date()


def _form_data(**overrides):
    today = _today()
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
    }
    data.update(overrides)
    return data


def _campaign(advertiser, status=Campaign.Status.DRAFT, **extra):
    fields = dict(
        advertiser=advertiser, name="Кампания на модерации", payment_type=FIXED,
        fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=status,
        deadline=_today() + timedelta(days=20),
    )
    fields.update(extra)
    return Campaign.objects.create(**fields)


# ── Общая проверка параметров ────────────────────────────────────────────────

class CampaignParamErrorsTest(SimpleTestCase):
    def _errors(self, **overrides):
        values = dict(
            payment_type=FIXED, fixed_price=Decimal("150000"), budget=Decimal("1500000"),
            start_date=date(2026, 11, 1), end_date=date(2026, 11, 30),
            deadline=date(2026, 11, 20), max_bloggers=0,
        )
        values.update(overrides)
        return campaign_param_errors(**values)

    def test_consistent_params_pass(self):
        self.assertEqual(self._errors(), {})

    def test_end_before_start(self):
        self.assertIn("end_date", self._errors(end_date=date(2026, 10, 31), deadline=None))

    def test_deadline_before_start(self):
        self.assertIn("deadline", self._errors(deadline=date(2026, 10, 31)))

    def test_deadline_after_end(self):
        self.assertIn("deadline", self._errors(deadline=date(2026, 12, 1)))

    def test_empty_dates_not_compared(self):
        self.assertEqual(self._errors(start_date=None, end_date=None, deadline=date(2026, 1, 1)), {})

    def test_price_above_budget(self):
        self.assertIn("fixed_price", self._errors(fixed_price=Decimal("2000000")))

    def test_bloggers_over_budget_names_fitting_count(self):
        errors = self._errors(max_bloggers=11)
        self.assertIn("max_bloggers", errors)
        self.assertIn("10", errors["max_bloggers"])

    def test_bloggers_within_budget(self):
        self.assertEqual(self._errors(max_bloggers=10), {})

    def test_no_blogger_limit_skips_budget_check(self):
        self.assertEqual(self._errors(max_bloggers=0), {})

    def test_cpa_skips_price_checks(self):
        self.assertEqual(
            self._errors(payment_type=Campaign.PaymentType.CPA, fixed_price=Decimal("9000000"), max_bloggers=50),
            {},
        )


# ── Фильтр money ─────────────────────────────────────────────────────────────

class MoneyFilterTest(SimpleTestCase):
    def test_int(self):
        self.assertEqual(money(1500000), "1 500 000")

    def test_decimal_zero_kopecks(self):
        self.assertEqual(money(Decimal("150000.00")), "150 000")

    def test_decimal_with_kopecks(self):
        self.assertEqual(money(Decimal("1500.5")), "1 500.50")

    def test_none(self):
        self.assertEqual(money(None), "")


# ── Веб-форма ────────────────────────────────────────────────────────────────

class CampaignFormTest(TestCase):
    def test_spaced_amounts_are_parsed(self):
        form = CampaignForm(data=_form_data(fixed_price="150 000", budget="1 500 000", min_subscribers="10 000"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["fixed_price"], Decimal("150000"))
        self.assertEqual(form.cleaned_data["budget"], Decimal("1500000"))
        self.assertEqual(form.cleaned_data["min_subscribers"], 10000)

    def test_nbsp_amounts_are_parsed(self):
        form = CampaignForm(data=_form_data(fixed_price="150 000", budget="1 500 000"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["budget"], Decimal("1500000"))

    def test_garbage_amount_is_field_error(self):
        form = CampaignForm(data=_form_data(budget="abc"))
        self.assertFalse(form.is_valid())
        self.assertIn("budget", form.errors)

    def test_end_before_start(self):
        today = _today()
        form = CampaignForm(data=_form_data(
            start_date=(today + timedelta(days=10)).isoformat(),
            end_date=(today + timedelta(days=5)).isoformat(),
            deadline="",
        ))
        self.assertFalse(form.is_valid())
        self.assertIn("end_date", form.errors)

    def test_deadline_outside_campaign(self):
        today = _today()
        form = CampaignForm(data=_form_data(deadline=(today + timedelta(days=40)).isoformat()))
        self.assertFalse(form.is_valid())
        self.assertIn("deadline", form.errors)

    def test_price_above_budget(self):
        form = CampaignForm(data=_form_data(fixed_price="2 000 000"))
        self.assertFalse(form.is_valid())
        self.assertIn("fixed_price", form.errors)

    def test_bloggers_over_budget(self):
        form = CampaignForm(data=_form_data(fixed_price="150 000", budget="1 500 000", max_bloggers="11"))
        self.assertFalse(form.is_valid())
        self.assertIn("max_bloggers", form.errors)
        self.assertIn("10", form.errors["max_bloggers"][0])

    def test_no_blogger_limit_passes(self):
        form = CampaignForm(data=_form_data(max_bloggers="0"))
        self.assertTrue(form.is_valid(), form.errors)


class CampaignCreatePageTest(TestCase):
    def setUp(self):
        self.adv = _user("adv_form@test.com", User.Role.ADVERTISER)
        self.client.force_login(self.adv)

    def test_page_has_number_inputs_and_script(self):
        r = self.client.get(reverse("web:campaign_create"))
        self.assertContains(r, "js/number-input.js")
        self.assertContains(r, 'name="budget"')
        self.assertContains(r, "data-number-input", count=5)
        self.assertContains(r, 'name="cpa_type"')
        self.assertContains(r, 'name="cpa_tracking_url"')

    def test_create_with_spaced_amounts(self):
        r = self.client.post(reverse("web:campaign_create"), _form_data(fixed_price="150 000", budget="1 500 000"))
        self.assertEqual(r.status_code, 302)
        campaign = Campaign.objects.get(advertiser=self.adv)
        self.assertEqual(campaign.fixed_price, Decimal("150000"))
        self.assertEqual(campaign.budget, Decimal("1500000"))

    def test_create_cpa_campaign_via_web(self):
        r = self.client.post(reverse("web:campaign_create"), _form_data(
            payment_type="cpa", fixed_price="", cpa_type="lead", cpa_rate="5 000",
            cpa_tracking_url="https://example.com/landing",
        ))
        self.assertEqual(r.status_code, 302)
        campaign = Campaign.objects.get(advertiser=self.adv)
        self.assertEqual(campaign.status, Campaign.Status.DRAFT)
        self.assertEqual(campaign.payment_type, Campaign.PaymentType.CPA)
        self.assertEqual(campaign.cpa_type, Campaign.CPAType.LEAD)
        self.assertEqual(campaign.cpa_rate, Decimal("5000"))
        self.assertEqual(campaign.cpa_tracking_url, "https://example.com/landing")

    def test_date_error_is_shown(self):
        today = _today()
        r = self.client.post(reverse("web:campaign_create"), _form_data(
            start_date=(today + timedelta(days=10)).isoformat(),
            end_date=(today + timedelta(days=5)).isoformat(),
            deadline="",
        ))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Дата окончания не может быть раньше даты начала.")
        self.assertFalse(Campaign.objects.filter(advertiser=self.adv).exists())

    def test_bloggers_error_is_shown(self):
        r = self.client.post(reverse("web:campaign_create"), _form_data(max_bloggers="11"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Бюджета хватает на 10 блогеров")


# ── API ──────────────────────────────────────────────────────────────────────

class CampaignApiValidationTest(TestCase):
    def setUp(self):
        self.adv = _user("adv_api@test.com", User.Role.ADVERTISER)
        self.api = APIClient()
        self.api.force_authenticate(self.adv)
        self.url = "/api/v1/campaigns/"

    def _payload(self, **overrides):
        today = _today()
        data = {
            "name": "API", "payment_type": "fixed",
            "fixed_price": "150000", "budget": "1500000",
            "start_date": (today + timedelta(days=1)).isoformat(),
            "end_date": (today + timedelta(days=30)).isoformat(),
            "deadline": (today + timedelta(days=20)).isoformat(),
            "max_bloggers": 10,
        }
        data.update(overrides)
        return data

    def test_bloggers_over_budget_400(self):
        r = self.api.post(self.url, self._payload(max_bloggers=11), format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("max_bloggers", r.json())
        self.assertFalse(Campaign.objects.exists())

    def test_valid_201(self):
        r = self.api.post(self.url, self._payload(), format="json")
        self.assertEqual(r.status_code, 201, r.content)

    def test_end_before_start_400(self):
        today = _today()
        r = self.api.post(self.url, self._payload(
            start_date=(today + timedelta(days=10)).isoformat(),
            end_date=(today + timedelta(days=5)).isoformat(),
            deadline=None,
        ), format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("end_date", r.json())

    def test_partial_update_uses_saved_values(self):
        campaign = _campaign(self.adv)
        r = self.api.patch(f"{self.url}{campaign.pk}/", {"max_bloggers": 11}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("max_bloggers", r.json())
        r = self.api.patch(f"{self.url}{campaign.pk}/", {"max_bloggers": 10}, format="json")
        self.assertEqual(r.status_code, 200, r.content)


# ── Повторная отправка отклонённой кампании ──────────────────────────────────

class CampaignResubmitTest(TestCase):
    def setUp(self):
        self.adv = _user("adv_resubmit@test.com", User.Role.ADVERTISER)

    def test_web_rejected_to_moderation_keeps_reason(self):
        campaign = _campaign(self.adv, status=Campaign.Status.REJECTED, rejection_reason="Нет описания")
        self.client.force_login(self.adv)
        self.client.post(reverse("web:campaign_submit", kwargs={"pk": campaign.pk}))
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.MODERATION)
        self.assertEqual(campaign.rejection_reason, "Нет описания")

    def test_web_active_not_submitted(self):
        campaign = _campaign(self.adv, status=Campaign.Status.ACTIVE)
        self.client.force_login(self.adv)
        self.client.post(reverse("web:campaign_submit", kwargs={"pk": campaign.pk}))
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.ACTIVE)

    def test_api_rejected_to_moderation(self):
        campaign = _campaign(self.adv, status=Campaign.Status.REJECTED, rejection_reason="Нет описания")
        api = APIClient()
        api.force_authenticate(self.adv)
        r = api.post(f"/api/v1/campaigns/{campaign.pk}/submit_for_moderation/")
        self.assertEqual(r.status_code, 200)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.MODERATION)

    def test_api_active_rejected_with_400(self):
        campaign = _campaign(self.adv, status=Campaign.Status.ACTIVE)
        api = APIClient()
        api.force_authenticate(self.adv)
        r = api.post(f"/api/v1/campaigns/{campaign.pk}/submit_for_moderation/")
        self.assertEqual(r.status_code, 400)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.ACTIVE)

    def test_owner_sees_rejection_reason(self):
        campaign = _campaign(self.adv, status=Campaign.Status.REJECTED, rejection_reason="Уточните целевую аудиторию")
        self.client.force_login(self.adv)
        r = self.client.get(reverse("web:campaign_detail", kwargs={"pk": campaign.pk}))
        self.assertContains(r, "Уточните целевую аудиторию")
        self.assertContains(r, "отправьте её на модерацию снова")
        self.assertContains(r, "1 500 000")


# ── Модерация ────────────────────────────────────────────────────────────────

class CampaignModerationTest(TestCase):
    def setUp(self):
        self.staff = _user("staff_mod@test.com", User.Role.ADVERTISER, is_staff=True)
        self.adv = _user("adv_mod@test.com", User.Role.ADVERTISER)
        self.blogger = _user("blogger_mod@test.com", User.Role.BLOGGER)
        self.campaign = _campaign(
            self.adv, status=Campaign.Status.MODERATION,
            description="Описание для модератора", max_bloggers=7,
        )
        self.staff_client = Client()
        self.staff_client.force_login(self.staff)

    def _reject(self, reason, **extra):
        return self.staff_client.post(
            reverse("web:admin_campaign_reject", kwargs={"pk": self.campaign.pk}),
            {"reason": reason, **extra},
        )

    def test_empty_reason_is_refused(self):
        for reason in ("", "   \n "):
            r = self._reject(reason)
            self.assertEqual(r.status_code, 302)
            self.campaign.refresh_from_db()
            self.assertEqual(self.campaign.status, Campaign.Status.MODERATION)
            self.assertEqual(self.campaign.rejection_reason, "")
        self.assertFalse(Notification.objects.filter(user=self.adv).exists())

    def test_empty_reason_from_card_returns_to_card(self):
        r = self._reject("", back="detail")
        self.assertRedirects(
            r, reverse("web:admin_campaign_detail", kwargs={"pk": self.campaign.pk}),
            fetch_redirect_response=False,
        )

    def test_reason_rejects_and_notifies(self):
        self._reject("Нет описания аудитории")
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.REJECTED)
        self.assertTrue(Notification.objects.filter(user=self.adv, body__contains="Нет описания аудитории").exists())

    def test_approve_after_resubmit_clears_reason(self):
        # Повторно отправленная кампания хранит прошлую причину до одобрения.
        self.campaign.rejection_reason = "Исправьте даты"
        self.campaign.save(update_fields=["rejection_reason"])
        self.staff_client.post(reverse("web:admin_campaign_approve", kwargs={"pk": self.campaign.pk}))
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.ACTIVE)
        self.assertEqual(self.campaign.rejection_reason, "")

    def test_card_shows_params_and_forms(self):
        r = self.staff_client.get(reverse("web:admin_campaign_detail", kwargs={"pk": self.campaign.pk}))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self.adv.email)
        self.assertContains(r, "Описание для модератора")
        self.assertContains(r, "150 000")
        self.assertContains(r, "1 500 000")
        self.assertContains(r, reverse("web:admin_campaign_approve", kwargs={"pk": self.campaign.pk}))
        self.assertContains(r, reverse("web:admin_campaign_reject", kwargs={"pk": self.campaign.pk}))
        self.assertContains(r, "required")

    def test_card_shows_last_rejection_reason(self):
        self.campaign.rejection_reason = "Исправьте даты"
        self.campaign.save(update_fields=["rejection_reason"])
        r = self.staff_client.get(reverse("web:admin_campaign_detail", kwargs={"pk": self.campaign.pk}))
        self.assertContains(r, "Исправьте даты")

    def test_active_card_has_no_actions(self):
        self.campaign.status = Campaign.Status.ACTIVE
        self.campaign.save(update_fields=["status"])
        r = self.staff_client.get(reverse("web:admin_campaign_detail", kwargs={"pk": self.campaign.pk}))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Описание для модератора")
        self.assertNotContains(r, reverse("web:admin_campaign_approve", kwargs={"pk": self.campaign.pk}))
        self.assertNotContains(r, reverse("web:admin_campaign_reject", kwargs={"pk": self.campaign.pk}))

    def test_non_staff_has_no_access(self):
        url = reverse("web:admin_campaign_detail", kwargs={"pk": self.campaign.pk})
        for user in (self.adv, self.blogger):
            c = Client()
            c.force_login(user)
            r = c.get(url)
            self.assertEqual(r.status_code, 302)
            self.assertNotContains(r, "Описание для модератора", status_code=302)

    def test_queue_shows_summary_and_link(self):
        r = self.staff_client.get(reverse("web:admin_campaigns"))
        self.assertContains(r, reverse("web:admin_campaign_detail", kwargs={"pk": self.campaign.pk}))
        self.assertContains(r, "Открыть")
        self.assertContains(r, "150 000")
        self.assertContains(r, "до 7")
