"""BZ-2: «Увеличить бюджет» без паузы и модерации (решение бизнеса 07.10.2026, п.1).

Владелец увеличивает бюджет кампании ACTIVE/PAUSED без паузы и модерации; новое значение
обязательно больше текущего; approved_snapshot синхронизируется, чтобы следующая
ре-модерация не показала бюджет как непроверенную правку.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.campaigns.models import Campaign
from apps.campaigns.services import AcceptError, increase_budget
from apps.users.models import User
from apps.web.campaign_proposals import changes_since_approval, mark_approved


def _user(email, role=User.Role.ADVERTISER):
    return User.objects.create_user(email=email, password="pass1234", role=role, status=User.Status.ACTIVE)


class IncreaseBudgetServiceTest(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.adv = _user("adv@test.com")
        self.other_adv = _user("other@test.com")

    def _campaign(self, status=Campaign.Status.ACTIVE, end_delta=30, budget="500000", **extra):
        fields = dict(
            advertiser=self.adv, name="К", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal(budget), status=status,
            start_date=self.today - timedelta(days=5), end_date=self.today + timedelta(days=end_delta),
        )
        fields.update(extra)
        campaign = Campaign.objects.create(**fields)
        mark_approved(campaign)
        campaign.save(update_fields=["approved_snapshot"])
        return campaign

    def test_increase_on_active_campaign(self):
        campaign = self._campaign(status=Campaign.Status.ACTIVE)
        increase_budget(campaign.pk, Decimal("700000"), self.adv)
        campaign.refresh_from_db()
        self.assertEqual(campaign.budget, Decimal("700000"))
        self.assertEqual(campaign.status, Campaign.Status.ACTIVE)

    def test_increase_on_paused_campaign(self):
        campaign = self._campaign(status=Campaign.Status.PAUSED)
        increase_budget(campaign.pk, Decimal("600000"), self.adv)
        campaign.refresh_from_db()
        self.assertEqual(campaign.budget, Decimal("600000"))
        self.assertEqual(campaign.status, Campaign.Status.PAUSED)

    def test_approved_snapshot_updated_no_pending_diff(self):
        campaign = self._campaign()
        increase_budget(campaign.pk, Decimal("900000"), self.adv)
        campaign.refresh_from_db()
        changes = changes_since_approval(campaign)
        self.assertEqual(changes, {})

    def test_not_owner_rejected(self):
        campaign = self._campaign()
        with self.assertRaises(AcceptError):
            increase_budget(campaign.pk, Decimal("900000"), self.other_adv)
        campaign.refresh_from_db()
        self.assertEqual(campaign.budget, Decimal("500000"))

    def test_value_not_greater_rejected(self):
        campaign = self._campaign(budget="500000")
        for value in (Decimal("500000"), Decimal("400000")):
            with self.assertRaises(AcceptError):
                increase_budget(campaign.pk, value, self.adv)
        campaign.refresh_from_db()
        self.assertEqual(campaign.budget, Decimal("500000"))

    def test_draft_campaign_rejected(self):
        campaign = self._campaign(status=Campaign.Status.DRAFT)
        with self.assertRaises(AcceptError):
            increase_budget(campaign.pk, Decimal("900000"), self.adv)

    def test_expired_campaign_rejected(self):
        campaign = self._campaign(end_delta=-1)
        with self.assertRaises(AcceptError):
            increase_budget(campaign.pk, Decimal("900000"), self.adv)


class IncreaseBudgetWebTest(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.adv = _user("adv@test.com")
        self.client.force_login(self.adv)

    def _campaign(self, **extra):
        fields = dict(
            advertiser=self.adv, name="К", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("500000"), status=Campaign.Status.ACTIVE,
            start_date=self.today - timedelta(days=5), end_date=self.today + timedelta(days=30),
        )
        fields.update(extra)
        return Campaign.objects.create(**fields)

    def test_web_increase_success(self):
        campaign = self._campaign()
        resp = self.client.post(
            reverse("web:campaign_increase_budget", args=[campaign.pk]), {"budget": "700 000"},
        )
        self.assertRedirects(resp, reverse("web:campaign_detail", args=[campaign.pk]))
        campaign.refresh_from_db()
        self.assertEqual(campaign.budget, Decimal("700000"))

    def test_web_increase_lower_value_ignored(self):
        campaign = self._campaign()
        self.client.post(reverse("web:campaign_increase_budget", args=[campaign.pk]), {"budget": "100 000"})
        campaign.refresh_from_db()
        self.assertEqual(campaign.budget, Decimal("500000"))


class IncreaseBudgetAPITest(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.adv = _user("adv@test.com")
        self.other_adv = _user("other@test.com")
        self.client = APIClient()
        self.client.force_authenticate(self.adv)

    def _campaign(self, advertiser=None, **extra):
        fields = dict(
            advertiser=advertiser or self.adv, name="К", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("500000"), status=Campaign.Status.ACTIVE,
            start_date=self.today - timedelta(days=5), end_date=self.today + timedelta(days=30),
        )
        fields.update(extra)
        return Campaign.objects.create(**fields)

    def test_api_increase_success(self):
        campaign = self._campaign()
        resp = self.client.post(f"/api/v1/campaigns/{campaign.pk}/increase-budget/", {"budget": "700000"})
        self.assertEqual(resp.status_code, 200)
        campaign.refresh_from_db()
        self.assertEqual(campaign.budget, Decimal("700000"))

    def test_api_increase_not_owner_403(self):
        campaign = self._campaign(advertiser=self.other_adv)
        resp = self.client.post(f"/api/v1/campaigns/{campaign.pk}/increase-budget/", {"budget": "900000"})
        self.assertEqual(resp.status_code, 404)

    def test_api_increase_lower_value_400(self):
        campaign = self._campaign()
        resp = self.client.post(f"/api/v1/campaigns/{campaign.pk}/increase-budget/", {"budget": "100000"})
        self.assertEqual(resp.status_code, 400)
        campaign.refresh_from_db()
        self.assertEqual(campaign.budget, Decimal("500000"))
