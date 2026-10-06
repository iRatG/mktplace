"""
QA camp_test_2 (06.10.2026), шаги 1 и 4: ошибки формы кампании у своих полей (без браузерной проверки),
видимые кнопки −/+, категория и «Что рекламируем» над описанием, понятный отказ от правок модератора.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.campaigns.models import Campaign, CampaignEditProposal
from apps.users.models import User
from apps.web.campaign_proposals import form_data_from_campaign


def _user(email, role=User.Role.ADVERTISER, is_staff=False):
    return User.objects.create_user(
        email=email, password="pass1234", role=role, status=User.Status.ACTIVE, is_staff=is_staff,
    )


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
    }
    data.update(overrides)
    return data


class FormErrorsAtOwnFieldsTest(TestCase):
    """Скрин 1: цена 5 000 при пустом бюджете — раньше браузер останавливал форму на бюджете."""

    def setUp(self):
        self.adv = _user("adv@test.com")
        self.client.force_login(self.adv)
        self.url = reverse("web:campaign_create")

    def test_form_is_novalidate(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn("novalidate", html)
        self.assertNotIn(" required>", html)

    def test_low_price_and_empty_budget_both_reported(self):
        r = self.client.post(self.url, _form_data(fixed_price="5 000", budget=""))
        self.assertEqual(r.status_code, 200)
        form = r.context["form"]
        self.assertIn("fixed_price", form.errors)
        self.assertIn("10 000", form.errors["fixed_price"][0])
        self.assertIn("budget", form.errors)
        self.assertFalse(Campaign.objects.exists())

    def test_empty_name_reported_by_server(self):
        r = self.client.post(self.url, _form_data(name=""))
        self.assertIn("name", r.context["form"].errors)

    def test_placeholders_read_as_examples(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('placeholder="например, 1 500 000"', html)
        self.assertIn('placeholder="например, 150 000"', html)


class NumberFieldsTest(TestCase):
    def setUp(self):
        self.adv = _user("adv@test.com")
        self.client.force_login(self.adv)

    def test_every_number_field_has_step_buttons(self):
        html = self.client.get(reverse("web:campaign_create")).content.decode()
        # цена, ставка CPA, бюджет, мин. подписчиков, макс. блогеров
        self.assertEqual(html.count("data-number-input"), 5)
        self.assertEqual(html.count('data-step-button="-1"'), 5)
        self.assertEqual(html.count('data-step-button="1"'), 5)
        self.assertNotIn('type="number"', html)

    def test_max_bloggers_accepts_spaces(self):
        self.client.post(
            reverse("web:campaign_create"),
            _form_data(fixed_price="10 000", budget="20 000 000", max_bloggers="1 000"),
        )
        self.assertEqual(Campaign.objects.get().max_bloggers, 1000)


class SubjectFieldTest(TestCase):
    def setUp(self):
        self.adv = _user("adv@test.com")
        self.staff = _user("staff@test.com", is_staff=True)
        self.client.force_login(self.adv)

    def test_category_and_subject_above_description(self):
        html = self.client.get(reverse("web:campaign_create")).content.decode()
        self.assertLess(html.index('name="category"'), html.index('name="subject"'))
        self.assertLess(html.index('name="subject"'), html.index('name="description"'))

    def test_spellcheck_on_text_fields(self):
        html = self.client.get(reverse("web:campaign_create")).content.decode()
        self.assertEqual(html.count('spellcheck="true"'), 3)

    def test_subject_saved_and_shown(self):
        self.client.post(reverse("web:campaign_create"), _form_data(subject="Крем для лица SPF 50"))
        campaign = Campaign.objects.get()
        self.assertEqual(campaign.subject, "Крем для лица SPF 50")
        self.assertContains(self.client.get(reverse("web:campaign_detail", kwargs={"pk": campaign.pk})),
                            "Крем для лица SPF 50")
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("web:admin_campaign_detail", kwargs={"pk": campaign.pk})),
                            "Крем для лица SPF 50")

    def test_subject_optional(self):
        self.client.post(reverse("web:campaign_create"), _form_data())
        self.assertEqual(Campaign.objects.get().subject, "")

    def test_subject_in_api(self):
        api = APIClient()
        api.force_authenticate(self.adv)
        r = api.post(reverse("campaigns:campaign-list"), {
            "name": "API", "payment_type": "fixed", "fixed_price": "150000", "budget": "1500000",
            "subject": "Кофейня на Чиланзаре",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Campaign.objects.get(name="API").subject, "Кофейня на Чиланзаре")

    def test_moderator_can_propose_subject(self):
        campaign = Campaign.objects.create(
            advertiser=self.adv, name="К", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=Campaign.Status.MODERATION,
        )
        data = form_data_from_campaign(campaign)
        payload = {k: data.getlist(k) for k in data}
        payload["subject"] = "Уточнённый товар"
        payload["proposal_comment"] = "Уточнил предмет рекламы"
        self.client.force_login(self.staff)
        self.client.post(reverse("web:admin_campaign_propose", kwargs={"pk": campaign.pk}), payload)
        proposal = CampaignEditProposal.objects.get()
        self.assertEqual(proposal.changes["subject"]["new"], "Уточнённый товар")
        self.client.force_login(self.adv)
        self.assertContains(self.client.get(reverse("web:campaign_detail", kwargs={"pk": campaign.pk})),
                            "Что рекламируем")


class DeclineProposalIsClearTest(TestCase):
    """Шаг 4: «Отклонить и исправить самому» читалось как «внести правки самостоятельно»."""

    def test_decline_button_named_and_confirmed(self):
        adv = _user("adv@test.com")
        staff = _user("staff@test.com", is_staff=True)
        campaign = Campaign.objects.create(
            advertiser=adv, name="К", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=Campaign.Status.MODERATION,
        )
        CampaignEditProposal.objects.create(
            campaign=campaign, author=staff, comment="Снизьте цену",
            changes={"fixed_price": {"old": "150000", "new": "120000"}},
        )
        self.client.force_login(adv)
        html = self.client.get(reverse("web:campaign_detail", kwargs={"pk": campaign.pk})).content.decode()
        self.assertIn(">Отклонить правки<", html)
        self.assertNotIn("Отклонить и исправить самому", html)
        self.assertIn("return confirm(", html)
        self.assertIn("окончательно", html)
