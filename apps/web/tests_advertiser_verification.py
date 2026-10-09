"""#40: отправить кампанию на модерацию может только подтверждённый рекламодатель."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.campaigns.models import Campaign
from apps.registration.models import LegalEntityApplication
from apps.users.models import User


def _advertiser(email="adv@test.com", **extra):
    return User.objects.create_user(
        email=email, password="pass1234", role=User.Role.ADVERTISER, status=User.Status.ACTIVE, **extra
    )


def _approve_legal_entity(advertiser):
    return LegalEntityApplication.objects.create(
        user=advertiser, company_name="ООО Ромашка", inn="123456789",
        status=LegalEntityApplication.Status.APPROVED,
    )


def _campaign(advertiser, status=Campaign.Status.DRAFT):
    today = timezone.localdate()
    return Campaign.objects.create(
        advertiser=advertiser, name="Кампания", payment_type=Campaign.PaymentType.FIXED,
        fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=status,
        start_date=today, end_date=today + timedelta(days=30), deadline=today + timedelta(days=20),
    )


class UnverifiedAdvertiserTest(TestCase):
    def test_web_submit_blocked(self):
        adv = _advertiser()
        campaign = _campaign(adv)
        self.client.force_login(adv)
        self.client.post(reverse("web:campaign_submit", kwargs={"pk": campaign.pk}))
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.DRAFT)

    def test_api_submit_blocked(self):
        adv = _advertiser()
        campaign = _campaign(adv)
        api = APIClient()
        api.force_authenticate(adv)
        r = api.post(f"/api/v1/campaigns/{campaign.pk}/submit_for_moderation/")
        self.assertEqual(r.status_code, 400)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.DRAFT)

    def test_detail_page_shows_message_instead_of_button(self):
        adv = _advertiser()
        campaign = _campaign(adv)
        self.client.force_login(adv)
        r = self.client.get(reverse("web:campaign_detail", kwargs={"pk": campaign.pk}))
        self.assertContains(r, "подтвердите аккаунт")
        self.assertNotContains(r, "Отправить на модерацию")

    def test_rejected_application_still_blocked(self):
        adv = _advertiser()
        LegalEntityApplication.objects.create(
            user=adv, company_name="ООО Ромашка", inn="123456789",
            status=LegalEntityApplication.Status.REJECTED,
        )
        campaign = _campaign(adv)
        self.client.force_login(adv)
        self.client.post(reverse("web:campaign_submit", kwargs={"pk": campaign.pk}))
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.DRAFT)


class VerifiedAdvertiserTest(TestCase):
    def test_web_submit_succeeds_with_approved_legal_entity(self):
        adv = _advertiser()
        _approve_legal_entity(adv)
        campaign = _campaign(adv)
        self.client.force_login(adv)
        self.client.post(reverse("web:campaign_submit", kwargs={"pk": campaign.pk}))
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.MODERATION)

    def test_api_submit_succeeds_with_approved_legal_entity(self):
        adv = _advertiser()
        _approve_legal_entity(adv)
        campaign = _campaign(adv)
        api = APIClient()
        api.force_authenticate(adv)
        r = api.post(f"/api/v1/campaigns/{campaign.pk}/submit_for_moderation/")
        self.assertEqual(r.status_code, 200)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.MODERATION)

    def test_demo_advertiser_is_exempt(self):
        adv = _advertiser(is_demo=True)
        campaign = _campaign(adv)
        self.client.force_login(adv)
        self.client.post(reverse("web:campaign_submit", kwargs={"pk": campaign.pk}))
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.MODERATION)

    def test_detail_page_shows_button(self):
        adv = _advertiser()
        _approve_legal_entity(adv)
        campaign = _campaign(adv)
        self.client.force_login(adv)
        r = self.client.get(reverse("web:campaign_detail", kwargs={"pk": campaign.pk}))
        self.assertContains(r, "Отправить на модерацию")

    def test_staff_sees_same_message_as_owner(self):
        adv = _advertiser()
        campaign = _campaign(adv)
        staff = _advertiser("staff@test.com", is_staff=True)
        self.client.force_login(staff)
        r = self.client.get(reverse("web:campaign_detail", kwargs={"pk": campaign.pk}))
        self.assertContains(r, "подтвердите аккаунт")
