"""
QA camp_test_3 (07.10.2026): ошибки формы кампании уходят при исправлении и выделяют любое поле (1.6, 12.2, 12.3),
легенда «*» (1.2), «Другое» — последняя категория (1.1), ошибка комментария модератора у поля (3.3),
сообщения об ошибках не исчезают сами (6.2).
"""
import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.campaigns.models import Campaign, CampaignEditProposal
from apps.platforms.models import OTHER_CATEGORY_SLUG, Category
from apps.users.models import User
from apps.web.campaign_proposals import form_data_from_campaign


def _user(email, role=User.Role.ADVERTISER, is_staff=False):
    return User.objects.create_user(
        email=email, password="pass1234", role=role, status=User.Status.ACTIVE, is_staff=is_staff,
    )


def _form_data(**overrides):
    today = timezone.now().date()
    data = {
        "name": "Кампания", "payment_type": "fixed", "fixed_price": "150000", "budget": "1500000",
        "start_date": (today + timedelta(days=1)).isoformat(),
        "end_date": (today + timedelta(days=30)).isoformat(),
        "deadline": (today + timedelta(days=20)).isoformat(),
        "min_subscribers": "0", "max_bloggers": "0",
    }
    data.update(overrides)
    return data


def _invalid_names(html):
    """Имена полей, помеченных aria-invalid="true"."""
    names = set()
    for tag in re.findall(r"<(?:input|select|textarea)\b[^>]*>", html, re.S):
        if 'aria-invalid="true"' in tag:
            names.add(re.search(r'name="([^"]+)"', tag).group(1))
    return names


class CampaignFormErrorsTest(TestCase):
    def setUp(self):
        self.client.force_login(_user("adv@test.com"))
        self.url = reverse("web:campaign_create")

    def test_errors_mark_fields_and_texts(self):
        html = self.client.post(self.url, _form_data(fixed_price="5000", budget="")).content.decode()
        self.assertEqual(_invalid_names(html), {"fixed_price", "budget"})
        self.assertIn('data-error-for="fixed_price"', html)
        self.assertIn('data-error-for="budget"', html)

    def test_date_error_marks_date_field(self):
        today = timezone.now().date()
        html = self.client.post(self.url, _form_data(
            start_date=(today + timedelta(days=10)).isoformat(), end_date=(today + timedelta(days=5)).isoformat(),
        )).content.decode()
        self.assertIn("end_date", _invalid_names(html))
        self.assertIn('data-error-for="end_date"', html)

    def test_form_declares_groups_legend_and_script(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('data-error-groups="fixed_price budget max_bloggers|', html)
        self.assertIn("* — обязательное поле", html)
        self.assertIn("js/form-errors.js", html)
        self.assertIn(f'data-min="{settings.CAMPAIGN_MIN_FIXED_PRICE}"', html)
        self.assertIn('data-min-hint-for="fixed_price"', html)

    def test_valid_form_has_no_invalid_marks(self):
        html = self.client.get(self.url).content.decode()
        self.assertEqual(_invalid_names(html), set())

    def test_scripts_clear_errors_and_mark_live(self):
        js = (Path(settings.BASE_DIR) / "static" / "js")
        errors = (js / "form-errors.js").read_text("utf-8")
        self.assertIn("data-error-for", errors)
        self.assertIn('removeAttribute("aria-invalid")', errors)
        number = (js / "number-input.js").read_text("utf-8")
        self.assertIn("setLiveInvalid(maxInput, !!over)", number)
        self.assertIn("checkMin", number)


class OtherCategoryLastTest(TestCase):
    def test_other_is_last_everywhere(self):
        self.assertTrue(Category.objects.filter(slug=OTHER_CATEGORY_SLUG).exists())
        Category.objects.create(name="Яхты", slug="yachts")  # по алфавиту после «Другое»
        slugs = list(Category.objects.values_list("slug", flat=True))
        self.assertEqual(slugs[-1], OTHER_CATEGORY_SLUG)
        self.assertLess(slugs.index("yachts"), slugs.index(OTHER_CATEGORY_SLUG))

    def test_campaign_form_lists_other_last(self):
        self.client.force_login(_user("adv@test.com"))
        html = self.client.get(reverse("web:campaign_create")).content.decode()
        select = html[html.index('name="category"'):html.index("</select>", html.index('name="category"'))]
        self.assertTrue(select.rstrip().endswith("Другое</option>"))


class ProposalCommentErrorTest(TestCase):
    def setUp(self):
        self.staff = _user("staff@test.com", is_staff=True)
        adv = _user("adv@test.com")
        today = timezone.now().date()
        self.campaign = Campaign.objects.create(
            advertiser=adv, name="Кампания", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=Campaign.Status.MODERATION,
            start_date=today + timedelta(days=1), end_date=today + timedelta(days=30),
            deadline=today + timedelta(days=20),
        )
        self.client.force_login(self.staff)
        self.url = reverse("web:admin_campaign_propose", args=[self.campaign.pk])

    def _payload(self, comment, **changes):
        data = form_data_from_campaign(self.campaign)
        payload = {k: data.getlist(k) for k in data}
        payload.update(changes)
        payload["proposal_comment"] = comment
        return payload

    def test_empty_comment_error_at_field(self):
        html = self.client.post(self.url, self._payload("", fixed_price="120000")).content.decode()
        self.assertFalse(CampaignEditProposal.objects.exists())
        self.assertIn('data-error-for="proposal_comment"', html)
        self.assertIn("proposal_comment", _invalid_names(html))

    def test_empty_comment_shown_together_with_form_errors(self):
        html = self.client.post(self.url, self._payload("", fixed_price="5000")).content.decode()
        self.assertIn('data-error-for="proposal_comment"', html)
        self.assertIn('data-error-for="fixed_price"', html)

    def test_with_comment_proposal_created(self):
        self.client.post(self.url, self._payload("Снизил цену", fixed_price="120000"))
        self.assertTrue(CampaignEditProposal.objects.exists())


class FlashMessagesTest(TestCase):
    def test_error_message_is_sticky_and_closable(self):
        staff = _user("staff@test.com", is_staff=True)
        adv = _user("adv@test.com")
        campaign = Campaign.objects.create(
            advertiser=adv, name="Кампания", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=Campaign.Status.ACTIVE,
        )
        self.client.force_login(staff)
        # предлагать правки можно только на модерации → сообщение об ошибке на странице карточки
        resp = self.client.get(reverse("web:admin_campaign_propose", args=[campaign.pk]), follow=True)
        html = resp.content.decode()
        self.assertIn("data-flash-sticky", html)
        self.assertIn("data-flash-close", html)
        self.assertIn("6000", html)
