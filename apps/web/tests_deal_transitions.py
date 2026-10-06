"""
#14: переходы сделки — одно место (apps/deals/services.py) для сайта, API, таймеров и панели.
Проверяем то, что раньше расходилось: журнал до смены статуса в таймерах, 72 ч от публикации, одинаковые правила
на сайте и в API, спор на сайте, принятие отклика без 500 при повторе.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.billing.models import Wallet
from apps.campaigns.models import Campaign
from apps.campaigns.models import Response as CampaignResponse
from apps.deals import services as transitions
from apps.deals.models import ChatMessage, Deal, DealStatusLog
from apps.deals.tasks import auto_approve_creative, auto_complete_deals
from apps.notifications.models import Notification
from apps.platforms.models import Platform
from apps.users.models import User

S = Deal.Status


def _user(email, role=User.Role.BLOGGER, **extra):
    return User.objects.create_user(email=email, password="pass1234", role=role, status=User.Status.ACTIVE, **extra)


class TransitionsTest(TestCase):
    def setUp(self):
        self.adv = _user("adv@test.com", User.Role.ADVERTISER)
        self.blogger = _user("bl@test.com")
        self.staff = _user("staff@test.com", User.Role.ADVERTISER, is_staff=True)
        self.campaign = Campaign.objects.create(
            advertiser=self.adv, name="К", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("100000"), budget=Decimal("1000000"), status=Campaign.Status.ACTIVE,
        )
        self.platform = Platform.objects.create(
            blogger=self.blogger, social_type=Platform.SocialType.INSTAGRAM, url="https://instagram.com/b",
            subscribers=1000, status=Platform.Status.APPROVED,
        )
        self.api = APIClient()

    def _deal(self, status=S.IN_PROGRESS, **extra):
        deal = Deal.objects.create(campaign=self.campaign, blogger=self.blogger, advertiser=self.adv,
                                   platform=self.platform, amount=Decimal("100000"), status=status, **extra)
        Wallet.objects.filter(user=self.adv).update(reserved_balance=Decimal("100000"))
        return deal

    # ── таймеры ──────────────────────────────────────────────────────────────
    def test_auto_complete_logs_before_status_and_counts_from_publication(self):
        now = timezone.now()
        overdue = self._deal(S.CHECKING, publication_at=now - timedelta(hours=73))
        fresh = self._deal(S.CHECKING, publication_at=now - timedelta(hours=10))
        Deal.objects.filter(pk=fresh.pk).update(updated_at=now - timedelta(days=10))  # старое сохранение не важно
        auto_complete_deals()
        overdue.refresh_from_db(); fresh.refresh_from_db()
        self.assertEqual((overdue.status, fresh.status), (S.COMPLETED, S.CHECKING))
        log = DealStatusLog.objects.filter(deal=overdue).latest("created_at")
        self.assertEqual((log.old_status, log.new_status), (S.CHECKING, S.COMPLETED))
        self.assertIsNotNone(overdue.last_distributed_at)
        self.assertTrue(Notification.objects.filter(user=self.blogger, related_deal=overdue).exists())

    def test_auto_approve_notifies_and_writes_chat(self):
        deal = self._deal(S.ON_APPROVAL, creative_submitted_at=timezone.now() - timedelta(hours=49))
        auto_approve_creative()
        deal.refresh_from_db()
        self.assertEqual(deal.status, S.WAITING_PUBLICATION)
        self.assertTrue(Notification.objects.filter(user=self.blogger, type=Notification.Type.CREATIVE_APPROVED).exists())
        self.assertTrue(ChatMessage.objects.filter(deal=deal, is_system=True).exists())

    # ── одинаковые правила сайт / API ────────────────────────────────────────
    def test_api_reject_creative_requires_reason(self):
        deal = self._deal(S.ON_APPROVAL)
        self.api.force_authenticate(self.adv)
        r = self.api.post(reverse("deals:deal-reject-creative", kwargs={"pk": deal.pk}), {})
        self.assertEqual(r.status_code, 400)
        deal.refresh_from_db()
        self.assertEqual(deal.status, S.ON_APPROVAL)

    def test_api_publication_url_scheme_checked(self):
        deal = self._deal(S.WAITING_PUBLICATION)
        self.api.force_authenticate(self.blogger)
        r = self.api.post(reverse("deals:deal-submit-publication", kwargs={"pk": deal.pk}),
                          {"publication_url": "instagram.com/p/1"})
        self.assertEqual(r.status_code, 400)

    def test_api_submit_creative_clears_reason_and_writes_chat(self):
        deal = self._deal(S.IN_PROGRESS, creative_rejection_reason="старое")
        self.api.force_authenticate(self.blogger)
        self.api.post(reverse("deals:deal-submit-creative", kwargs={"pk": deal.pk}), {"creative_text": "новый"})
        deal.refresh_from_db()
        self.assertEqual((deal.status, deal.creative_rejection_reason), (S.ON_APPROVAL, ""))
        self.assertTrue(ChatMessage.objects.filter(deal=deal, is_system=True).exists())

    def test_wrong_side_cannot_transition(self):
        deal = self._deal(S.ON_APPROVAL)
        with self.assertRaises(transitions.TransitionError):
            transitions.approve_creative(deal.pk, self.blogger)

    def test_api_chat_read_only_after_completion(self):
        deal = self._deal(S.COMPLETED)
        self.api.force_authenticate(self.blogger)
        r = self.api.post(reverse("deals:deal-messages", kwargs={"deal_id": deal.pk}), {"deal": deal.pk, "text": "hi"})
        self.assertEqual(r.status_code, 400)

    # ── спор ─────────────────────────────────────────────────────────────────
    def test_web_dispute_opens_and_notifies(self):
        deal = self._deal(S.CHECKING, publication_at=timezone.now())
        self.client.force_login(self.adv)
        self.client.post(reverse("web:deal_dispute", kwargs={"pk": deal.pk}), {"reason": "Пост удалён"})
        deal.refresh_from_db()
        self.assertEqual((deal.status, deal.is_frozen, deal.dispute_reason), (S.DISPUTED, True, "Пост удалён"))
        self.assertTrue(Notification.objects.filter(user=self.blogger, type=Notification.Type.DEAL_DISPUTED).exists())
        self.assertTrue(Notification.objects.filter(user=self.staff, type=Notification.Type.DEAL_DISPUTED).exists())

    def test_web_dispute_requires_reason_and_checking(self):
        deal = self._deal(S.CHECKING, publication_at=timezone.now())
        self.client.force_login(self.adv)
        self.client.post(reverse("web:deal_dispute", kwargs={"pk": deal.pk}), {"reason": ""})
        deal.refresh_from_db()
        self.assertEqual(deal.status, S.CHECKING)
        in_progress = self._deal(S.IN_PROGRESS)
        self.client.post(reverse("web:deal_dispute", kwargs={"pk": in_progress.pk}), {"reason": "x"})
        in_progress.refresh_from_db()
        self.assertEqual(in_progress.status, S.IN_PROGRESS)

    def test_dispute_resolution_pays_and_notifies_both(self):
        deal = self._deal(S.CHECKING, publication_at=timezone.now())
        transitions.open_dispute(deal.pk, self.blogger, "Не подтверждают")
        self.client.force_login(self.staff)
        self.client.post(reverse("web:admin_dispute_resolve", kwargs={"pk": deal.pk}),
                         {"resolution": "complete", "comment": "Пост на месте"})
        deal.refresh_from_db()
        self.assertEqual(deal.status, S.COMPLETED)
        self.assertIsNotNone(deal.last_distributed_at)
        for user in (self.adv, self.blogger):
            self.assertTrue(Notification.objects.filter(user=user, title="Спор разрешён").exists())

    # ── принятие отклика ─────────────────────────────────────────────────────
    def test_double_accept_is_an_error_not_500(self):
        Wallet.objects.filter(user=self.adv).update(available_balance=Decimal("1000000"))
        resp = CampaignResponse.objects.create(campaign=self.campaign, blogger=self.blogger, platform=self.platform)
        self.client.force_login(self.adv)
        first = self.client.post(reverse("web:response_accept", kwargs={"pk": resp.pk}))
        second = self.client.post(reverse("web:response_accept", kwargs={"pk": resp.pk}))
        self.assertEqual((first.status_code, second.status_code), (302, 302))
        self.assertEqual(Deal.objects.count(), 1)

    def test_api_response_requires_blogger_and_approved_platform(self):
        pending = Platform.objects.create(blogger=self.blogger, social_type=Platform.SocialType.TELEGRAM,
                                          url="https://t.me/b", subscribers=1, status=Platform.Status.PENDING)
        self.api.force_authenticate(self.blogger)
        r = self.api.post(reverse("campaigns:response-list"),
                          {"campaign": self.campaign.pk, "platform": pending.pk, "content_type": "post"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.api.force_authenticate(self.adv)
        r = self.api.post(reverse("campaigns:response-list"),
                          {"campaign": self.campaign.pk, "platform": self.platform.pk, "content_type": "post"}, format="json")
        self.assertEqual(r.status_code, 400)
