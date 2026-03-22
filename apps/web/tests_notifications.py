"""
Tests for Module 11А — In-app notifications.
Covers: NotificationService, context processor unread count,
notification list page, mark-all-read, triggers in key views.
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.billing.models import Wallet
from apps.campaigns.models import Campaign, DirectOffer
from apps.notifications.models import Notification
from apps.notifications.service import NotificationService
from apps.platforms.models import Platform
from apps.users.models import User


def _make_user(email, role, confirmed=True):
    u = User.objects.create_user(email=email, password="Test1234!", role=role)
    if confirmed:
        u.status = User.Status.ACTIVE
        u.save(update_fields=["status"])
    return u


_counter = 0


def _make_platform(blogger, status=Platform.Status.APPROVED):
    global _counter
    _counter += 1
    return Platform.objects.create(
        blogger=blogger,
        social_type=Platform.SocialType.INSTAGRAM,
        url=f"https://instagram.com/test{_counter}",
        subscribers=10000,
        avg_views=500,
        engagement_rate=Decimal("3.50"),
        price_post=Decimal("50000"),
        status=status,
    )


def _make_campaign(advertiser, status=Campaign.Status.ACTIVE):
    return Campaign.objects.create(
        advertiser=advertiser,
        name="Test Campaign",
        description="desc",
        payment_type=Campaign.PaymentType.FIXED,
        fixed_price=Decimal("50000"),
        budget=Decimal("500000"),
        status=status,
    )


def _fund_wallet(user, amount=Decimal("500000")):
    w, _ = Wallet.objects.get_or_create(user=user)
    w.available_balance = amount
    w.save(update_fields=["available_balance"])
    return w


# ── NotificationService unit tests ────────────────────────────────────────────

class NotificationServiceTest(TestCase):
    """NotificationService creates Notification objects correctly."""

    def setUp(self):
        self.advertiser = _make_user("adv@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("bl@test.com", User.Role.BLOGGER)
        self.campaign = _make_campaign(self.advertiser)
        self.platform = _make_platform(self.blogger)

    def test_notify_creates_notification(self):
        NotificationService.notify(
            self.blogger,
            Notification.Type.SYSTEM,
            "Test", "Body text",
        )
        n = Notification.objects.get(user=self.blogger)
        self.assertEqual(n.title, "Test")
        self.assertEqual(n.body, "Body text")
        self.assertFalse(n.is_read)

    def test_notify_new_response(self):
        NotificationService.notify_new_response(self.advertiser, self.campaign, self.blogger)
        n = Notification.objects.get(user=self.advertiser)
        self.assertEqual(n.type, Notification.Type.CAMPAIGN_RESPONSE)
        self.assertIn(self.blogger.email, n.body)

    def test_notify_response_accepted(self):
        from apps.deals.models import Deal
        deal = Deal.objects.create(
            campaign=self.campaign, blogger=self.blogger,
            platform=self.platform, advertiser=self.advertiser,
            amount=Decimal("50000"), status=Deal.Status.IN_PROGRESS,
        )
        NotificationService.notify_response_accepted(self.blogger, self.campaign, deal)
        n = Notification.objects.get(user=self.blogger)
        self.assertEqual(n.type, Notification.Type.RESPONSE_ACCEPTED)
        self.assertEqual(n.related_deal, deal)

    def test_notify_response_rejected(self):
        NotificationService.notify_response_rejected(self.blogger, self.campaign)
        n = Notification.objects.get(user=self.blogger)
        self.assertEqual(n.type, Notification.Type.RESPONSE_REJECTED)

    def test_notify_direct_offer_received(self):
        NotificationService.notify_direct_offer_received(self.blogger, self.campaign, self.advertiser)
        n = Notification.objects.get(user=self.blogger)
        self.assertEqual(n.type, Notification.Type.DIRECT_OFFER_RECEIVED)

    def test_notify_direct_offer_accepted(self):
        from apps.deals.models import Deal
        deal = Deal.objects.create(
            campaign=self.campaign, blogger=self.blogger,
            platform=self.platform, advertiser=self.advertiser,
            amount=Decimal("50000"), status=Deal.Status.IN_PROGRESS,
        )
        NotificationService.notify_direct_offer_accepted(self.advertiser, self.campaign, self.blogger, deal)
        n = Notification.objects.get(user=self.advertiser)
        self.assertEqual(n.type, Notification.Type.DIRECT_OFFER_ACCEPTED)

    def test_notify_direct_offer_rejected(self):
        NotificationService.notify_direct_offer_rejected(self.advertiser, self.campaign, self.blogger)
        n = Notification.objects.get(user=self.advertiser)
        self.assertEqual(n.type, Notification.Type.DIRECT_OFFER_REJECTED)

    def test_notify_campaign_approved(self):
        NotificationService.notify_campaign_approved(self.advertiser, self.campaign)
        n = Notification.objects.get(user=self.advertiser)
        self.assertEqual(n.type, Notification.Type.CAMPAIGN_STATUS)
        self.assertIn("опубликована", n.title)

    def test_notify_campaign_rejected(self):
        self.campaign.rejection_reason = "Нарушение правил"
        self.campaign.save(update_fields=["rejection_reason"])
        NotificationService.notify_campaign_rejected(self.advertiser, self.campaign)
        n = Notification.objects.get(user=self.advertiser)
        self.assertIn("Нарушение правил", n.body)

    def test_notify_platform_approved(self):
        NotificationService.notify_platform_approved(self.blogger, self.platform)
        n = Notification.objects.get(user=self.blogger)
        self.assertEqual(n.type, Notification.Type.PLATFORM_MODERATED)
        self.assertIn("одобрена", n.title)

    def test_notify_withdrawal_approved(self):
        NotificationService.notify_withdrawal_approved(self.blogger, Decimal("65000"))
        n = Notification.objects.get(user=self.blogger)
        self.assertEqual(n.type, Notification.Type.WITHDRAWAL_APPROVED)

    def test_notify_withdrawal_rejected(self):
        NotificationService.notify_withdrawal_rejected(self.blogger, Decimal("65000"), "Недостаточно данных")
        n = Notification.objects.get(user=self.blogger)
        self.assertEqual(n.type, Notification.Type.WITHDRAWAL_REJECTED)
        self.assertIn("Недостаточно данных", n.body)

    def test_notify_deal_cancelled_notifies_other_party(self):
        """Отмена рекламодателем → уведомление блогеру."""
        from apps.deals.models import Deal
        deal = Deal.objects.create(
            campaign=self.campaign, blogger=self.blogger,
            platform=self.platform, advertiser=self.advertiser,
            amount=Decimal("50000"), status=Deal.Status.IN_PROGRESS,
        )
        NotificationService.notify_deal_cancelled(deal, cancelled_by=self.advertiser)
        self.assertEqual(Notification.objects.filter(user=self.blogger).count(), 1)
        self.assertEqual(Notification.objects.filter(user=self.advertiser).count(), 0)


# ── Context processor unread count ────────────────────────────────────────────

class NotificationContextProcessorTest(TestCase):
    """unread_notifications_count in template context."""

    def setUp(self):
        self.user = _make_user("u@test.com", User.Role.BLOGGER)
        self.client.force_login(self.user)

    def test_zero_by_default(self):
        r = self.client.get(reverse("web:blogger_dashboard"))
        self.assertEqual(r.context["unread_notifications_count"], 0)

    def test_count_increases_with_unread(self):
        Notification.objects.create(
            user=self.user, type=Notification.Type.SYSTEM,
            title="T", body="B",
        )
        Wallet.objects.get_or_create(user=self.user)
        r = self.client.get(reverse("web:blogger_dashboard"))
        self.assertEqual(r.context["unread_notifications_count"], 1)

    def test_read_notifications_not_counted(self):
        Notification.objects.create(
            user=self.user, type=Notification.Type.SYSTEM,
            title="T", body="B", is_read=True,
        )
        Wallet.objects.get_or_create(user=self.user)
        r = self.client.get(reverse("web:blogger_dashboard"))
        self.assertEqual(r.context["unread_notifications_count"], 0)


# ── Notification list page ─────────────────────────────────────────────────────

class NotificationListPageTest(TestCase):
    """GET /notifications/ — показывает уведомления и помечает как прочитанные."""

    def setUp(self):
        self.user = _make_user("u@test.com", User.Role.BLOGGER)
        self.client.force_login(self.user)
        self.url = reverse("web:notifications")

    def test_page_200(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)

    def test_anonymous_redirected(self):
        self.client.logout()
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 302)

    def test_notifications_shown(self):
        Notification.objects.create(user=self.user, type=Notification.Type.SYSTEM, title="Hello", body="World")
        r = self.client.get(self.url)
        self.assertEqual(len(r.context["notifications"]), 1)

    def test_opening_marks_all_read(self):
        Notification.objects.create(user=self.user, type=Notification.Type.SYSTEM, title="T", body="B")
        self.assertEqual(Notification.objects.filter(user=self.user, is_read=False).count(), 1)
        self.client.get(self.url)
        self.assertEqual(Notification.objects.filter(user=self.user, is_read=False).count(), 0)

    def test_unread_count_in_context(self):
        Notification.objects.create(user=self.user, type=Notification.Type.SYSTEM, title="T", body="B")
        r = self.client.get(self.url)
        self.assertEqual(r.context["unread_count"], 1)

    def test_mark_all_read_post(self):
        Notification.objects.create(user=self.user, type=Notification.Type.SYSTEM, title="T", body="B")
        r = self.client.post(reverse("web:notifications_mark_all_read"))
        self.assertRedirects(r, reverse("web:notifications"))
        self.assertEqual(Notification.objects.filter(user=self.user, is_read=False).count(), 0)


# ── Trigger integration tests ──────────────────────────────────────────────────

class NotificationTriggerTest(TestCase):
    """Проверяет что уведомления создаются при ключевых событиях."""

    def setUp(self):
        self.advertiser = _make_user("adv@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("bl@test.com", User.Role.BLOGGER)
        self.platform = _make_platform(self.blogger)
        self.campaign = _make_campaign(self.advertiser)
        _fund_wallet(self.advertiser)
        Wallet.objects.get_or_create(user=self.blogger)

    def test_campaign_respond_notifies_advertiser(self):
        self.client.force_login(self.blogger)
        self.client.post(
            reverse("web:campaign_respond", kwargs={"pk": self.campaign.pk}),
            {"platform": self.platform.pk, "content_type": "post"},
        )
        self.assertEqual(
            Notification.objects.filter(user=self.advertiser, type=Notification.Type.CAMPAIGN_RESPONSE).count(), 1
        )

    def test_response_accept_notifies_blogger(self):
        from apps.campaigns.models import Response as CampaignResponse
        resp = CampaignResponse.objects.create(
            blogger=self.blogger, campaign=self.campaign,
            platform=self.platform, content_type="post",
            proposed_price=Decimal("50000"),
        )
        self.client.force_login(self.advertiser)
        self.client.post(reverse("web:response_accept", kwargs={"pk": resp.pk}))
        self.assertEqual(
            Notification.objects.filter(user=self.blogger, type=Notification.Type.RESPONSE_ACCEPTED).count(), 1
        )

    def test_response_reject_notifies_blogger(self):
        from apps.campaigns.models import Response as CampaignResponse
        resp = CampaignResponse.objects.create(
            blogger=self.blogger, campaign=self.campaign,
            platform=self.platform, content_type="post",
        )
        self.client.force_login(self.advertiser)
        self.client.post(reverse("web:response_reject", kwargs={"pk": resp.pk}))
        self.assertEqual(
            Notification.objects.filter(user=self.blogger, type=Notification.Type.RESPONSE_REJECTED).count(), 1
        )

    def test_direct_offer_create_notifies_blogger(self):
        self.client.force_login(self.advertiser)
        self.client.post(
            reverse("web:direct_offer_create", kwargs={"platform_pk": self.platform.pk}),
            {"campaign": self.campaign.pk, "content_type": "post"},
        )
        self.assertEqual(
            Notification.objects.filter(user=self.blogger, type=Notification.Type.DIRECT_OFFER_RECEIVED).count(), 1
        )

    def test_direct_offer_reject_notifies_advertiser(self):
        offer = DirectOffer.objects.create(
            advertiser=self.advertiser, blogger=self.blogger,
            campaign=self.campaign, platform=self.platform,
            content_type="post",
        )
        self.client.force_login(self.blogger)
        self.client.post(reverse("web:direct_offer_reject", kwargs={"pk": offer.pk}))
        self.assertEqual(
            Notification.objects.filter(user=self.advertiser, type=Notification.Type.DIRECT_OFFER_REJECTED).count(), 1
        )

    def test_direct_offer_accept_notifies_advertiser(self):
        offer = DirectOffer.objects.create(
            advertiser=self.advertiser, blogger=self.blogger,
            campaign=self.campaign, platform=self.platform,
            content_type="post", proposed_price=Decimal("50000"),
        )
        self.client.force_login(self.blogger)
        self.client.post(reverse("web:direct_offer_accept", kwargs={"pk": offer.pk}))
        self.assertEqual(
            Notification.objects.filter(user=self.advertiser, type=Notification.Type.DIRECT_OFFER_ACCEPTED).count(), 1
        )
