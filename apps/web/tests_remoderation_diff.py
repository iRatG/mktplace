"""
QA camp_test_2 (06.10.2026), шаг 11: при повторной модерации после правки на паузе модератор видел только
новые условия. Теперь при одобрении сохраняется снимок, и карточка показывает «было → стало».
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.campaigns.testing import CARD_FIELDS
from apps.campaigns.models import Campaign, CampaignEditProposal
from apps.users.models import User
from apps.web.campaign_proposals import changes_since_approval, form_data_from_campaign


def _user(email, is_staff=False, is_demo=False):
    return User.objects.create_user(
        email=email, password="pass1234", role=User.Role.ADVERTISER,
        status=User.Status.ACTIVE, is_staff=is_staff, is_demo=is_demo,
    )


def _campaign(advertiser, status=Campaign.Status.MODERATION):
    today = timezone.now().date()
    return Campaign.objects.create(
        advertiser=advertiser, name="Кампания", payment_type=Campaign.PaymentType.FIXED,
        fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=status,
        start_date=today + timedelta(days=1), end_date=today + timedelta(days=30),
        deadline=today + timedelta(days=20), **CARD_FIELDS,
    )


def _edit_payload(campaign, **changes):
    data = form_data_from_campaign(campaign)
    payload = {k: data.getlist(k) for k in data}
    payload.update(changes)
    return payload


class RemoderationDiffTest(TestCase):
    def setUp(self):
        self.adv = _user("adv@test.com", is_demo=True)
        self.staff = _user("staff@test.com", is_staff=True)
        self.campaign = _campaign(self.adv)

    def _approve(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("web:admin_campaign_approve", kwargs={"pk": self.campaign.pk}))
        self.campaign.refresh_from_db()

    def _pause_and_edit(self, **changes):
        self.client.force_login(self.adv)
        self.client.post(reverse("web:campaign_pause", kwargs={"pk": self.campaign.pk}))
        self.campaign.refresh_from_db()
        self.client.post(reverse("web:campaign_edit", kwargs={"pk": self.campaign.pk}),
                         _edit_payload(self.campaign, **changes))
        self.campaign.refresh_from_db()

    def test_approval_stores_snapshot(self):
        self._approve()
        self.assertEqual(self.campaign.status, Campaign.Status.ACTIVE)
        self.assertEqual(self.campaign.approved_snapshot["fixed_price"], "150000")

    def test_moderator_sees_what_changed_after_pause_edit(self):
        self._approve()
        self._pause_and_edit(max_bloggers="1", description="Новое описание")
        self.assertEqual(self.campaign.status, Campaign.Status.MODERATION)
        self.assertEqual(set(changes_since_approval(self.campaign)), {"max_bloggers", "description"})

        self.client.force_login(self.staff)
        r = self.client.get(reverse("web:admin_campaign_detail", kwargs={"pk": self.campaign.pk}))
        self.assertContains(r, "что изменилось с последнего одобрения")
        rows = r.context["since_approval_rows"]
        self.assertIn(("Макс. блогеров", "0", "1"), rows)
        self.assertIn(("Описание", "Пост о креме", "Новое описание"), rows)

    def test_spaced_amount_same_value_is_not_a_change(self):
        self._approve()
        self._pause_and_edit(fixed_price="150 000", description="x")
        self.assertNotIn("fixed_price", changes_since_approval(self.campaign))

    def test_first_moderation_shows_no_diff_block(self):
        self.client.force_login(self.staff)
        r = self.client.get(reverse("web:admin_campaign_detail", kwargs={"pk": self.campaign.pk}))
        self.assertIsNone(r.context["since_approval_rows"])
        self.assertNotContains(r, "что изменилось с последнего одобрения")

    def test_accepted_proposal_also_stores_snapshot(self):
        CampaignEditProposal.objects.create(
            campaign=self.campaign, author=self.staff, comment="Снизьте цену",
            changes={"fixed_price": {"old": "150000", "new": "120000"}},
        )
        self.client.force_login(self.adv)
        self.client.post(reverse("web:campaign_proposal_accept", kwargs={"pk": self.campaign.pk}))
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.ACTIVE)
        self.assertEqual(self.campaign.approved_snapshot["fixed_price"], "120000")

    def test_resubmit_after_rejection_compares_with_last_approval(self):
        self._approve()
        self._pause_and_edit(budget="2000000")
        self.client.force_login(self.staff)
        self.client.post(reverse("web:admin_campaign_reject", kwargs={"pk": self.campaign.pk}),
                         {"reason": "Слишком большой бюджет"})
        self.client.force_login(self.adv)
        self.client.post(reverse("web:campaign_submit", kwargs={"pk": self.campaign.pk}))
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.MODERATION)
        self.assertIn("budget", changes_since_approval(self.campaign))
