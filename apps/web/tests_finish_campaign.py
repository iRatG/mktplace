"""
Решение бизнеса 07.10.2026 (вопрос 4 — Б, BZ-4, #32): «Завершить кампанию» досрочно — те же последствия, что по сроку.
Р7: можно и из «На модерации», если кампания уже была одобрена; ожидающее предложение правок закрывается.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.billing.models import Wallet
from apps.campaigns.models import Campaign, CampaignEditProposal, DirectOffer
from apps.campaigns.models import Response as CampaignResponse
from apps.campaigns.services import complete_expired_campaigns
from apps.deals.models import Deal
from apps.notifications.models import Notification
from apps.platforms.models import Platform
from apps.users.models import User

_n = 0


def _user(email, role, **extra):
    return User.objects.create_user(email=email, password="pass1234", role=role, status=User.Status.ACTIVE, **extra)


def _platform(blogger):
    global _n
    _n += 1
    return Platform.objects.create(
        blogger=blogger, social_type=Platform.SocialType.INSTAGRAM, url=f"https://instagram.com/f{_n}",
        subscribers=12000, price_post=Decimal("50000"), status=Platform.Status.APPROVED,
    )


class FinishCampaignTest(TestCase):
    def setUp(self):
        self.adv = _user("adv@test.com", User.Role.ADVERTISER)
        Wallet.objects.filter(user=self.adv).update(available_balance=Decimal("10000000"))
        self.campaign = Campaign.objects.create(
            advertiser=self.adv, name="Кампания", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=Campaign.Status.ACTIVE,
            approved_snapshot={"name": "Кампания"},
        )
        self.b_deal = _user("deal@test.com", User.Role.BLOGGER)
        self.b_wait = _user("wait@test.com", User.Role.BLOGGER)
        self.b_offer = _user("offer@test.com", User.Role.BLOGGER)
        self.finish_url = reverse("web:campaign_finish", kwargs={"pk": self.campaign.pk})

    def _deal(self):
        resp = CampaignResponse.objects.create(
            campaign=self.campaign, blogger=self.b_deal, platform=_platform(self.b_deal), content_type="post",
        )
        self.client.force_login(self.adv)
        self.client.post(reverse("web:response_accept", kwargs={"pk": resp.pk}))
        return Deal.objects.get()

    def _pending(self):
        resp = CampaignResponse.objects.create(
            campaign=self.campaign, blogger=self.b_wait, platform=_platform(self.b_wait), content_type="post",
        )
        offer = DirectOffer.objects.create(
            advertiser=self.adv, blogger=self.b_offer, campaign=self.campaign,
            platform=_platform(self.b_offer), content_type="post",
        )
        return resp, offer

    def _finish(self):
        self.client.force_login(self.adv)
        return self.client.post(self.finish_url)

    # ── Досрочно на сайте ────────────────────────────────────────────────────

    def test_finish_active_campaign_same_consequences(self):
        deal = self._deal()
        resp, offer = self._pending()
        self._finish()
        self.campaign.refresh_from_db()
        resp.refresh_from_db()
        offer.refresh_from_db()
        deal.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.COMPLETED)
        self.assertIsNotNone(self.campaign.completed_early_at)
        self.assertEqual(resp.status, CampaignResponse.Status.EXPIRED)
        self.assertEqual(offer.status, DirectOffer.Status.EXPIRED)
        self.assertEqual(deal.status, Deal.Status.IN_PROGRESS)
        self.assertTrue(Notification.objects.filter(user=self.b_wait).exists())
        self.assertTrue(Notification.objects.filter(user=self.b_offer).exists())
        continues = Notification.objects.get(user=self.b_deal, title="Кампания завершена — ваша сделка продолжается")
        self.assertEqual(continues.related_deal, deal)
        self.assertTrue(Notification.objects.filter(user=self.adv, title="Кампания завершена досрочно").exists())

    def test_finish_paused_campaign(self):
        self.campaign.status = Campaign.Status.PAUSED
        self.campaign.save(update_fields=["status"])
        self._finish()
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.COMPLETED)

    def test_finish_moderation_after_approval_closes_edit_proposal(self):
        moderator = _user("mod@test.com", User.Role.ADVERTISER, is_staff=True)
        self.campaign.status = Campaign.Status.MODERATION
        self.campaign.save(update_fields=["status"])
        proposal = CampaignEditProposal.objects.create(
            campaign=self.campaign, author=moderator, changes={}, comment="Уточните описание",
        )
        self.client.force_login(self.adv)
        page = self.client.get(reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk}))
        self.assertContains(page, "Предложение правок модератора закроется")
        self._finish()
        self.campaign.refresh_from_db()
        proposal.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.COMPLETED)
        self.assertEqual(proposal.status, CampaignEditProposal.Status.CLOSED)
        note = Notification.objects.get(user=moderator, title="Предложение правок закрыто")
        self.assertEqual(note.url, reverse("web:admin_campaign_detail", kwargs={"pk": self.campaign.pk}))

    def test_first_moderation_and_draft_not_finished(self):
        for status in (Campaign.Status.MODERATION, Campaign.Status.DRAFT):
            self.campaign.status = status
            self.campaign.approved_snapshot = None
            self.campaign.save(update_fields=["status", "approved_snapshot"])
            r = self._finish()
            self.campaign.refresh_from_db()
            self.assertEqual(self.campaign.status, status)
            msgs = [str(m) for m in r.wsgi_request._messages]
            self.assertTrue(any("Завершить можно" in m for m in msgs), msgs)

    def test_other_advertiser_cannot_finish(self):
        other = _user("other@test.com", User.Role.ADVERTISER)
        self.client.force_login(other)
        self.assertEqual(self.client.post(self.finish_url).status_code, 404)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.ACTIVE)

    def test_button_with_consequences_and_early_mark(self):
        self.client.force_login(self.adv)
        page = self.client.get(reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk}))
        self.assertContains(page, "Завершить кампанию")
        self.assertContains(page, "Начатые сделки продолжатся до конца и оплаты")
        self._finish()
        page = self.client.get(reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk}))
        self.assertContains(page, f"досрочно {timezone.localdate():%d.%m.%Y}")
        self.assertNotContains(page, "Да, завершить")

    # ── API ──────────────────────────────────────────────────────────────────

    def _api(self, action):
        api = APIClient()
        api.force_authenticate(self.adv)
        return api.post(reverse(f"campaigns:campaign-{action}", kwargs={"pk": self.campaign.pk}))

    def test_api_complete(self):
        resp, _ = self._pending()
        self.assertEqual(self._api("complete").status_code, 200)
        self.campaign.refresh_from_db()
        resp.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.COMPLETED)
        self.assertEqual(resp.status, CampaignResponse.Status.EXPIRED)

    def test_api_cancel_does_the_same(self):
        resp, _ = self._pending()
        self.assertEqual(self._api("cancel").status_code, 200)
        self.campaign.refresh_from_db()
        resp.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.COMPLETED)
        self.assertIsNotNone(self.campaign.completed_early_at)
        self.assertEqual(resp.status, CampaignResponse.Status.EXPIRED)

    def test_api_complete_draft_refused(self):
        self.campaign.status = Campaign.Status.DRAFT
        self.campaign.save(update_fields=["status"])
        self.assertEqual(self._api("complete").status_code, 400)

    # ── По сроку — та же функция, без отметки «досрочно» ────────────────────

    def test_timer_completion_notifies_running_deals_without_early_mark(self):
        deal = self._deal()
        Campaign.objects.filter(pk=self.campaign.pk).update(end_date=timezone.localdate() - timedelta(days=1))
        self.assertEqual(complete_expired_campaigns(), 1)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.COMPLETED)
        self.assertIsNone(self.campaign.completed_early_at)
        self.assertTrue(Notification.objects.filter(
            user=self.b_deal, related_deal=deal, title="Кампания завершена — ваша сделка продолжается",
        ).exists())
