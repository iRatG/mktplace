"""
Решение бизнеса 06.10.2026 (опросник по camp_test_2, вопросы 2–3): рекламодатель отклоняет отклик с комментарием,
блогер может откликнуться на ту же кампанию снова; ждать решения может только один его отклик.
"""
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.campaigns.models import Campaign
from apps.campaigns.models import Response as CampaignResponse
from apps.notifications.models import Notification
from apps.platforms.models import Platform
from apps.users.models import User

PENDING = CampaignResponse.Status.PENDING
REJECTED = CampaignResponse.Status.REJECTED


def _user(email, role):
    return User.objects.create_user(email=email, password="pass1234", role=role, status=User.Status.ACTIVE)


class RepeatResponseTest(TestCase):
    def setUp(self):
        self.adv = _user("adv@test.com", User.Role.ADVERTISER)
        self.blogger = _user("bl@test.com", User.Role.BLOGGER)
        self.campaign = Campaign.objects.create(
            advertiser=self.adv, name="Кампания", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=Campaign.Status.ACTIVE,
        )
        self.platform = Platform.objects.create(
            blogger=self.blogger, social_type=Platform.SocialType.INSTAGRAM, url="https://instagram.com/b",
            subscribers=12000, price_post=Decimal("50000"), status=Platform.Status.APPROVED,
        )
        self.detail_url = reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk})
        self.respond_url = reverse("web:campaign_respond", kwargs={"pk": self.campaign.pk})

    def _respond(self, price="200000"):
        self.client.force_login(self.blogger)
        return self.client.post(self.respond_url, {
            "platform": self.platform.pk, "content_type": "post", "proposed_price": price,
        })

    def _reject(self, resp, reason=""):
        self.client.force_login(self.adv)
        return self.client.post(reverse("web:response_reject", kwargs={"pk": resp.pk}), {"reason": reason})

    def test_reject_with_comment_reaches_blogger(self):
        self._respond()
        resp = CampaignResponse.objects.get()
        self._reject(resp, "Готовы на 150 000")
        resp.refresh_from_db()
        self.assertEqual(resp.status, REJECTED)
        self.assertEqual(resp.rejection_reason, "Готовы на 150 000")
        body = Notification.objects.get(user=self.blogger, type=Notification.Type.RESPONSE_REJECTED).body
        self.assertIn("Готовы на 150 000", body)
        self.assertIn("откликнуться снова", body)
        self.client.force_login(self.blogger)
        self.assertContains(self.client.get(reverse("web:my_responses")), "Готовы на 150 000")

    def test_blogger_can_respond_again_after_rejection(self):
        self._respond("200000")
        self._reject(CampaignResponse.objects.get(), "Дорого")
        self.client.force_login(self.blogger)
        page = self.client.get(self.detail_url)
        self.assertFalse(page.context["already_responded"])
        self.assertContains(page, "Ваш прошлый отклик отклонён")
        self.assertContains(page, "Дорого")
        self._respond("150000")
        self.assertEqual(CampaignResponse.objects.filter(status=PENDING).count(), 1)
        self.assertEqual(CampaignResponse.objects.get(status=PENDING).proposed_price, Decimal("150000"))

    def test_second_response_blocked_while_pending(self):
        self._respond()
        self._respond("150000")
        self.assertEqual(CampaignResponse.objects.count(), 1)

    def test_blocked_while_accepted(self):
        CampaignResponse.objects.create(
            campaign=self.campaign, blogger=self.blogger, platform=self.platform,
            status=CampaignResponse.Status.ACCEPTED,
        )
        self._respond()
        self.assertEqual(CampaignResponse.objects.count(), 1)

    def test_db_allows_only_one_pending_per_campaign(self):
        CampaignResponse.objects.create(campaign=self.campaign, blogger=self.blogger, platform=self.platform)
        other = Platform.objects.create(
            blogger=self.blogger, social_type=Platform.SocialType.TELEGRAM, url="https://t.me/b",
            subscribers=1000, status=Platform.Status.APPROVED,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            CampaignResponse.objects.create(campaign=self.campaign, blogger=self.blogger, platform=other)

    def test_api_reject_with_reason_and_respond_again(self):
        resp = CampaignResponse.objects.create(campaign=self.campaign, blogger=self.blogger, platform=self.platform)
        api = APIClient()
        api.force_authenticate(self.adv)
        api.post(reverse("campaigns:response-reject", kwargs={"pk": resp.pk}), {"reason": "Нужен Reels"})
        resp.refresh_from_db()
        self.assertEqual(resp.rejection_reason, "Нужен Reels")

        api.force_authenticate(self.blogger)
        payload = {"campaign": self.campaign.pk, "platform": self.platform.pk, "content_type": "reels"}
        created = self._api_create(api, payload)
        self.assertEqual(created.status_code, 201, created.content)
        again = self._api_create(api, payload)
        self.assertEqual(again.status_code, 400)

    def _api_create(self, api, payload):
        return api.post(reverse("campaigns:response-list"), payload, format="json")

    def test_api_response_routes_resolve_to_responses(self):
        """/api/v1/campaigns/responses/ раньше попадал в campaign-detail с pk="responses"."""
        from django.urls import resolve

        self.assertEqual(resolve(reverse("campaigns:response-list")).url_name, "response-list")
        api = APIClient()
        api.force_authenticate(self.blogger)
        self.assertEqual(api.get(reverse("campaigns:response-list")).status_code, 200)
