"""
Решение бизнеса 07.10.2026 (вопрос 5 — Б, BZ-3, #31): на ответ по отклику и прямому предложению — 7 дней, потом
EXPIRED; за сутки до срока — одно напоминание тому, кто должен ответить; после истечения обе стороны уведомлены,
блогер может откликнуться снова. Срок идёт и на паузе (Р6).
"""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.billing.models import Wallet
from apps.campaigns.models import RESPONSE_TTL, Campaign, DirectOffer
from apps.campaigns.models import Response as CampaignResponse
from apps.campaigns.services import expire_overdue_responses_and_offers, send_response_offer_reminders
from apps.campaigns.tasks import auto_expire_responses_and_offers
from apps.deals.models import Deal
from apps.notifications.models import Notification
from apps.platforms.models import Platform
from apps.users.models import User

PENDING = CampaignResponse.Status.PENDING
EXPIRED = CampaignResponse.Status.EXPIRED


def _user(email, role):
    return User.objects.create_user(email=email, password="pass1234", role=role, status=User.Status.ACTIVE)


class ResponseExpiryTest(TestCase):
    def setUp(self):
        self.adv = _user("adv@test.com", User.Role.ADVERTISER)
        Wallet.objects.filter(user=self.adv).update(available_balance=Decimal("10000000"))
        self.blogger = _user("bl@test.com", User.Role.BLOGGER)
        self.campaign = Campaign.objects.create(
            advertiser=self.adv, name="Кампания", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=Campaign.Status.ACTIVE,
        )
        self.platform = Platform.objects.create(
            blogger=self.blogger, social_type=Platform.SocialType.INSTAGRAM, url="https://instagram.com/b",
            subscribers=12000, price_post=Decimal("50000"), status=Platform.Status.APPROVED,
        )

    def _response(self, **kwargs):
        return CampaignResponse.objects.create(
            campaign=self.campaign, blogger=self.blogger, platform=self.platform, content_type="post", **kwargs,
        )

    def _offer(self, **kwargs):
        return DirectOffer.objects.create(
            advertiser=self.adv, blogger=self.blogger, campaign=self.campaign, platform=self.platform,
            content_type="post", **kwargs,
        )

    def _overdue(self):
        return timezone.now() - timedelta(minutes=1)

    # ── Срок ──────────────────────────────────────────────────────────────────

    def test_deadline_is_seven_days_from_creation(self):
        before = timezone.now()
        resp = self._response()
        offer = self._offer()
        for item in (resp, offer):
            self.assertGreaterEqual(item.expires_at, before + RESPONSE_TTL)
            self.assertLessEqual(item.expires_at, timezone.now() + RESPONSE_TTL)
            self.assertFalse(item.is_overdue)

    def test_task_in_beat_schedule(self):
        tasks = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}
        self.assertIn("apps.campaigns.tasks.auto_expire_responses_and_offers", tasks)

    # ── Истечение ─────────────────────────────────────────────────────────────

    def test_overdue_response_expires_and_both_sides_notified(self):
        resp = self._response(expires_at=self._overdue())
        fresh = CampaignResponse.objects.create(
            campaign=self.campaign, blogger=_user("b2@test.com", User.Role.BLOGGER),
            platform=self.platform, content_type="post",
        )
        self.assertEqual(expire_overdue_responses_and_offers(), (1, 0))
        resp.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual(resp.status, EXPIRED)
        self.assertEqual(fresh.status, PENDING)
        blogger_note = Notification.objects.get(user=self.blogger)
        self.assertIn("откликнуться снова", blogger_note.body)
        self.assertEqual(blogger_note.url, reverse("web:my_responses"))
        adv_note = Notification.objects.get(user=self.adv)
        self.assertEqual(adv_note.url, reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk}))

    def test_overdue_offer_expires_and_advertiser_may_send_again(self):
        offer = self._offer(expires_at=self._overdue())
        auto_expire_responses_and_offers()
        offer.refresh_from_db()
        self.assertEqual(offer.status, DirectOffer.Status.EXPIRED)
        self.assertTrue(Notification.objects.filter(user=self.blogger, title="Предложение истекло").exists())
        adv_note = Notification.objects.get(user=self.adv)
        self.assertIn("новое предложение", adv_note.body)
        self.assertEqual(adv_note.url, reverse("web:direct_offer_create", kwargs={"platform_pk": self.platform.pk}))
        # ограничение уникальности не мешает новому предложению
        self._offer()
        self.assertEqual(DirectOffer.objects.filter(status=DirectOffer.Status.PENDING).count(), 1)

    def test_paused_campaign_response_still_expires(self):
        self.campaign.status = Campaign.Status.PAUSED
        self.campaign.save(update_fields=["status"])
        resp = self._response(expires_at=self._overdue())
        expire_overdue_responses_and_offers()
        resp.refresh_from_db()
        self.assertEqual(resp.status, EXPIRED)

    def test_blogger_can_respond_again_after_expiry(self):
        resp = self._response(expires_at=self._overdue())
        expire_overdue_responses_and_offers()
        self.client.force_login(self.blogger)
        page = self.client.get(reverse("web:my_responses"))
        self.assertContains(page, "Срок ответа истёк — можно откликнуться снова")
        self.client.post(reverse("web:campaign_respond", kwargs={"pk": self.campaign.pk}), {
            "platform": self.platform.pk, "content_type": "post", "proposed_price": "150000",
        })
        self.assertEqual(CampaignResponse.objects.filter(status=PENDING).exclude(pk=resp.pk).count(), 1)

    # ── Принятие с прошедшим сроком ──────────────────────────────────────────

    def test_web_accept_overdue_response_refused(self):
        resp = self._response(expires_at=self._overdue())
        self.client.force_login(self.adv)
        r = self.client.post(reverse("web:response_accept", kwargs={"pk": resp.pk}))
        resp.refresh_from_db()
        self.assertEqual(resp.status, PENDING)
        self.assertFalse(Deal.objects.exists())
        msgs = [str(m) for m in r.wsgi_request._messages]
        self.assertTrue(any("Срок ответа на отклик истёк" in m for m in msgs), msgs)

    def test_api_accept_overdue_response_refused(self):
        resp = self._response(expires_at=self._overdue())
        api = APIClient()
        api.force_authenticate(self.adv)
        r = api.post(reverse("campaigns:response-accept", kwargs={"pk": resp.pk}))
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Deal.objects.exists())

    def test_accept_overdue_offer_refused(self):
        offer = self._offer(expires_at=self._overdue())
        self.client.force_login(self.blogger)
        self.client.post(reverse("web:direct_offer_accept", kwargs={"pk": offer.pk}))
        offer.refresh_from_db()
        self.assertEqual(offer.status, DirectOffer.Status.PENDING)
        self.assertFalse(Deal.objects.exists())

    def test_accept_in_time_still_works(self):
        resp = self._response()
        self.client.force_login(self.adv)
        self.client.post(reverse("web:response_accept", kwargs={"pk": resp.pk}))
        self.assertEqual(Deal.objects.count(), 1)

    # ── Напоминание ──────────────────────────────────────────────────────────

    def test_reminder_once_to_the_side_that_must_answer(self):
        soon = timezone.now() + timedelta(hours=5)
        resp = self._response(expires_at=soon)
        offer = self._offer(expires_at=soon)
        CampaignResponse.objects.create(  # срок через 7 дней — напоминать рано
            campaign=self.campaign, blogger=_user("b3@test.com", User.Role.BLOGGER),
            platform=self.platform, content_type="post",
        )
        self.assertEqual(send_response_offer_reminders(), 2)
        self.assertEqual(send_response_offer_reminders(), 0)
        adv_notes = Notification.objects.filter(user=self.adv, title="Ответьте на отклик")
        self.assertEqual(adv_notes.count(), 1)
        self.assertIn(timezone.localtime(soon).strftime("%d.%m.%Y %H:%M"), adv_notes.get().body)
        self.assertEqual(Notification.objects.filter(user=self.blogger, title="Ответьте на предложение").count(), 1)
        resp.refresh_from_db()
        offer.refresh_from_db()
        self.assertIsNotNone(resp.reminder_sent_at)
        self.assertIsNotNone(offer.reminder_sent_at)

    # ── Интерфейс ────────────────────────────────────────────────────────────

    def test_deadline_shown_to_both_sides(self):
        resp = self._response()
        offer = self._offer()
        deadline = timezone.localtime(resp.expires_at).strftime("%d.%m.%Y %H:%M")
        self.client.force_login(self.adv)
        self.assertContains(self.client.get(reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk})),
                            f"ответить до {deadline}")
        self.client.force_login(self.blogger)
        self.assertContains(self.client.get(reverse("web:my_responses")), f"ответ до {deadline}")
        dashboard = self.client.get(reverse("web:blogger_dashboard"))
        self.assertContains(dashboard, f"ответить до {timezone.localtime(offer.expires_at):%d.%m.%Y %H:%M}")

    def test_overdue_offer_hidden_from_dashboard(self):
        self._offer(expires_at=self._overdue())
        self.client.force_login(self.blogger)
        self.assertEqual(list(self.client.get(reverse("web:blogger_dashboard")).context["incoming_offers"]), [])
