"""
#18: кампания с истёкшим сроком завершается таймером (он раньше не был в расписании). Ожидающие отклики
и предложения закрываются, сделки продолжаются; просроченную кампанию нельзя одобрить, возобновить или
заключить по ней сделку; даты в прошлом нельзя ставить при создании или правке.
"""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.campaigns.models import Campaign, DirectOffer
from apps.campaigns.models import Response as CampaignResponse
from apps.campaigns.services import complete_expired_campaigns
from apps.campaigns.tasks import auto_complete_expired_campaigns
from apps.deals.models import Deal
from apps.notifications.models import Notification
from apps.platforms.models import Platform
from apps.users.models import User


def _user(email, role=User.Role.ADVERTISER, is_staff=False):
    return User.objects.create_user(email=email, password="pass1234", role=role, status=User.Status.ACTIVE, is_staff=is_staff)


class CampaignExpiryTest(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.adv = _user("adv@test.com")
        self.blogger = _user("bl@test.com", User.Role.BLOGGER)
        self.platform = Platform.objects.create(
            blogger=self.blogger, social_type=Platform.SocialType.INSTAGRAM, url="https://instagram.com/b",
            subscribers=1000, status=Platform.Status.APPROVED,
        )

    def _campaign(self, status=Campaign.Status.ACTIVE, end_delta=-1, **extra):
        fields = dict(
            advertiser=self.adv, name="К", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=status,
            start_date=self.today - timedelta(days=30), end_date=self.today + timedelta(days=end_delta),
        )
        fields.update(extra)
        return Campaign.objects.create(**fields)

    def test_task_is_scheduled(self):
        tasks = {e["task"] for e in settings.CELERY_BEAT_SCHEDULE.values()}
        self.assertIn("apps.campaigns.tasks.auto_complete_expired_campaigns", tasks)

    def test_expired_active_and_paused_completed_current_untouched(self):
        expired = self._campaign()
        paused = self._campaign(status=Campaign.Status.PAUSED)
        today_end = self._campaign(end_delta=0)
        no_end = self._campaign(end_date=None)
        draft = self._campaign(status=Campaign.Status.DRAFT)
        self.assertIn("2", auto_complete_expired_campaigns())
        statuses = {c.pk: c.status for c in Campaign.objects.all()}
        self.assertEqual(statuses[expired.pk], Campaign.Status.COMPLETED)
        self.assertEqual(statuses[paused.pk], Campaign.Status.COMPLETED)
        self.assertEqual(statuses[today_end.pk], Campaign.Status.ACTIVE)
        self.assertEqual(statuses[no_end.pk], Campaign.Status.ACTIVE)
        self.assertEqual(statuses[draft.pk], Campaign.Status.DRAFT)

    def test_pending_closed_deals_untouched_everyone_notified(self):
        campaign = self._campaign()
        resp = CampaignResponse.objects.create(campaign=campaign, blogger=self.blogger, platform=self.platform)
        offer = DirectOffer.objects.create(advertiser=self.adv, blogger=self.blogger, campaign=campaign, platform=self.platform)
        deal = Deal.objects.create(campaign=campaign, blogger=self.blogger, advertiser=self.adv,
                                   platform=self.platform, amount=Decimal("150000"), status=Deal.Status.IN_PROGRESS)
        complete_expired_campaigns()
        resp.refresh_from_db(); offer.refresh_from_db(); deal.refresh_from_db()
        self.assertEqual(resp.status, CampaignResponse.Status.EXPIRED)
        self.assertEqual(offer.status, DirectOffer.Status.EXPIRED)
        self.assertEqual(deal.status, Deal.Status.IN_PROGRESS)
        self.assertTrue(Notification.objects.filter(user=self.adv, title="Кампания завершена").exists())
        # отклик и предложение закрыты + «ваша сделка продолжается» (BZ-4, #32)
        self.assertEqual(Notification.objects.filter(user=self.blogger).count(), 3)
        self.assertTrue(Notification.objects.filter(user=self.blogger, related_deal=deal).exists())

    def test_idempotent(self):
        self._campaign()
        self.assertEqual(complete_expired_campaigns(), 1)
        self.assertEqual(complete_expired_campaigns(), 0)

    def test_expired_campaign_cannot_be_approved_or_resumed(self):
        moderation = self._campaign(status=Campaign.Status.MODERATION)
        staff = _user("staff@test.com", is_staff=True)
        self.client.force_login(staff)
        self.client.post(reverse("web:admin_campaign_approve", kwargs={"pk": moderation.pk}))
        moderation.refresh_from_db()
        self.assertEqual(moderation.status, Campaign.Status.MODERATION)

        paused = self._campaign(status=Campaign.Status.PAUSED)
        self.client.force_login(self.adv)
        self.client.post(reverse("web:campaign_resume", kwargs={"pk": paused.pk}))
        paused.refresh_from_db()
        self.assertEqual(paused.status, Campaign.Status.PAUSED)
        api = APIClient()
        api.force_authenticate(self.adv)
        r = api.post(reverse("campaigns:campaign-resume", kwargs={"pk": paused.pk}))
        self.assertEqual(r.status_code, 400)

    def test_no_deal_on_expired_campaign_before_timer_runs(self):
        campaign = self._campaign()  # ещё ACTIVE: таймер не успел
        resp = CampaignResponse.objects.create(campaign=campaign, blogger=self.blogger, platform=self.platform,
                                               proposed_price=Decimal("150000"))
        self.client.force_login(self.adv)
        self.client.post(reverse("web:response_accept", kwargs={"pk": resp.pk}))
        self.assertFalse(Deal.objects.exists())

    def test_past_dates_rejected_on_create_web_and_api(self):
        self.client.force_login(self.adv)
        r = self.client.post(reverse("web:campaign_create"), {
            "name": "K", "payment_type": "fixed", "fixed_price": "150000", "budget": "1500000",
            "start_date": (self.today - timedelta(days=1)).isoformat(),
            "end_date": (self.today - timedelta(days=1)).isoformat(),
            "min_subscribers": "0", "max_bloggers": "0",
        })
        errors = r.context["form"].errors
        self.assertIn("start_date", errors)
        self.assertIn("end_date", errors)
        api = APIClient()
        api.force_authenticate(self.adv)
        r = api.post(reverse("campaigns:campaign-list"), {
            "name": "API", "payment_type": "fixed", "fixed_price": "150000", "budget": "1500000",
            "end_date": (self.today - timedelta(days=1)).isoformat(),
        }, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("end_date", r.json())
