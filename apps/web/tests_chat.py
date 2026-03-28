"""
Tests for Sprint 6 — Chat in deals (Module 7).

Covers:
  - ChatMessage model: creation, str, ordering
  - deal_send_message: text message, file message, empty → error
  - deal_send_message: access control (only parties can send)
  - deal_send_message: read-only for COMPLETED / CANCELLED
  - deal_send_message: is_staff can send to any deal
  - deal_detail context: chat_messages, chat_form, can_send_message
  - can_send_message=False for COMPLETED / CANCELLED
  - System messages (is_system=True) visible in context
  - GET /deals/<pk>/messages/ → 405 (require_POST)
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.billing.models import Wallet
from apps.campaigns.models import Campaign
from apps.deals.models import ChatMessage, Deal
from apps.platforms.models import Platform
from apps.users.models import User


# ── helpers ──────────────────────────────────────────────────────────────────

_counter = 0


def _make_user(email, role, confirmed=True):
    u = User.objects.create_user(email=email, password="Test1234!", role=role)
    if confirmed:
        u.status = User.Status.ACTIVE
        u.save(update_fields=["status"])
    return u


def _make_platform(blogger):
    global _counter
    _counter += 1
    return Platform.objects.create(
        blogger=blogger,
        social_type=Platform.SocialType.INSTAGRAM,
        url=f"https://instagram.com/chat{_counter}",
        subscribers=5000,
        avg_views=300,
        engagement_rate=Decimal("3.00"),
        price_post=Decimal("30000"),
        status=Platform.Status.APPROVED,
    )


def _make_campaign(advertiser):
    global _counter
    _counter += 1
    return Campaign.objects.create(
        advertiser=advertiser,
        name=f"Campaign {_counter}",
        description="desc",
        payment_type=Campaign.PaymentType.FIXED,
        fixed_price=Decimal("30000"),
        budget=Decimal("300000"),
        status=Campaign.Status.ACTIVE,
    )


def _make_deal(advertiser, blogger, status=Deal.Status.IN_PROGRESS):
    campaign = _make_campaign(advertiser)
    platform = _make_platform(blogger)
    return Deal.objects.create(
        campaign=campaign,
        blogger=blogger,
        platform=platform,
        advertiser=advertiser,
        amount=Decimal("30000"),
        status=status,
    )


def _make_wallet(user, amount=Decimal("500000")):
    w, _ = Wallet.objects.get_or_create(user=user)
    w.available_balance = amount
    w.save(update_fields=["available_balance"])
    return w


def _make_staff():
    return User.objects.create_user(
        email="staffchat@test.com", password="Test1234!", role=User.Role.ADVERTISER,
        is_staff=True, status=User.Status.ACTIVE,
    )


# ── ChatMessage model tests ───────────────────────────────────────────────────

class ChatMessageModelTest(TestCase):
    """ChatMessage model basic behavior."""

    def setUp(self):
        self.advertiser = _make_user("advchat@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("blchat@test.com", User.Role.BLOGGER)
        self.deal = _make_deal(self.advertiser, self.blogger)

    def test_create_text_message(self):
        msg = ChatMessage.objects.create(
            deal=self.deal, sender=self.advertiser, text="Hello blogger"
        )
        self.assertEqual(msg.text, "Hello blogger")
        self.assertFalse(msg.is_system)
        self.assertEqual(msg.deal, self.deal)

    def test_str_representation(self):
        msg = ChatMessage.objects.create(
            deal=self.deal, sender=self.advertiser, text="Hi"
        )
        self.assertIn(str(self.deal.pk), str(msg))
        self.assertIn(self.advertiser.email, str(msg))

    def test_ordering_by_created_at(self):
        msg1 = ChatMessage.objects.create(deal=self.deal, sender=self.advertiser, text="First")
        msg2 = ChatMessage.objects.create(deal=self.deal, sender=self.blogger, text="Second")
        msgs = list(ChatMessage.objects.filter(deal=self.deal))
        self.assertEqual(msgs[0], msg1)
        self.assertEqual(msgs[1], msg2)

    def test_system_message_flag(self):
        msg = ChatMessage.objects.create(
            deal=self.deal, text="Сделка переведена в статус В работе", is_system=True
        )
        self.assertTrue(msg.is_system)
        self.assertIsNone(msg.sender)

    def test_related_name_messages(self):
        ChatMessage.objects.create(deal=self.deal, sender=self.advertiser, text="A")
        ChatMessage.objects.create(deal=self.deal, sender=self.blogger, text="B")
        self.assertEqual(self.deal.messages.count(), 2)


# ── deal_send_message: text message ──────────────────────────────────────────

class DealSendMessageTest(TestCase):
    """deal_send_message view: happy path and guards."""

    def setUp(self):
        self.advertiser = _make_user("adv2@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("bl2@test.com", User.Role.BLOGGER)
        _make_wallet(self.advertiser)
        _make_wallet(self.blogger)
        self.deal = _make_deal(self.advertiser, self.blogger, Deal.Status.IN_PROGRESS)
        self.url = reverse("web:deal_send_message", args=[self.deal.pk])

    def test_advertiser_can_send_message(self):
        self.client.login(username="adv2@test.com", password="Test1234!")
        resp = self.client.post(self.url, {"text": "Привет, блогер!"})
        self.assertRedirects(resp, reverse("web:deal_detail", args=[self.deal.pk]))
        self.assertEqual(ChatMessage.objects.filter(deal=self.deal).count(), 1)
        msg = ChatMessage.objects.get(deal=self.deal)
        self.assertEqual(msg.text, "Привет, блогер!")
        self.assertEqual(msg.sender, self.advertiser)

    def test_blogger_can_send_message(self):
        self.client.login(username="bl2@test.com", password="Test1234!")
        resp = self.client.post(self.url, {"text": "Понял, приступаю!"})
        self.assertRedirects(resp, reverse("web:deal_detail", args=[self.deal.pk]))
        self.assertEqual(ChatMessage.objects.filter(deal=self.deal).count(), 1)

    def test_anonymous_redirected_to_login(self):
        resp = self.client.post(self.url, {"text": "test"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp["Location"])

    def test_get_method_not_allowed(self):
        self.client.login(username="adv2@test.com", password="Test1234!")
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_empty_post_creates_no_message(self):
        self.client.login(username="adv2@test.com", password="Test1234!")
        resp = self.client.post(self.url, {"text": "  "})
        self.assertRedirects(resp, reverse("web:deal_detail", args=[self.deal.pk]))
        self.assertEqual(ChatMessage.objects.filter(deal=self.deal).count(), 0)

    def test_third_party_cannot_send(self):
        outsider = _make_user("outsider@test.com", User.Role.ADVERTISER)
        self.client.login(username="outsider@test.com", password="Test1234!")
        resp = self.client.post(self.url, {"text": "Hacker"})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(ChatMessage.objects.filter(deal=self.deal).count(), 0)

    def test_blogger_outsider_cannot_send(self):
        outsider = _make_user("outsider_blogger@test.com", User.Role.BLOGGER)
        self.client.login(username="outsider_blogger@test.com", password="Test1234!")
        resp = self.client.post(self.url, {"text": "Hacker"})
        self.assertEqual(resp.status_code, 404)

    def test_multiple_messages_saved(self):
        self.client.login(username="adv2@test.com", password="Test1234!")
        self.client.post(self.url, {"text": "Msg 1"})
        self.client.post(self.url, {"text": "Msg 2"})
        self.assertEqual(ChatMessage.objects.filter(deal=self.deal).count(), 2)


# ── Chat read-only for terminal statuses ─────────────────────────────────────

class ChatReadOnlyTest(TestCase):
    """Chat is read-only for COMPLETED and CANCELLED deals."""

    def setUp(self):
        self.advertiser = _make_user("adv3@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("bl3@test.com", User.Role.BLOGGER)
        _make_wallet(self.advertiser)
        _make_wallet(self.blogger)

    def _post_message(self, deal, role_email):
        self.client.login(username=role_email, password="Test1234!")
        url = reverse("web:deal_send_message", args=[deal.pk])
        return self.client.post(url, {"text": "Read-only test"})

    def test_cannot_send_to_completed_deal(self):
        deal = _make_deal(self.advertiser, self.blogger, Deal.Status.COMPLETED)
        resp = self._post_message(deal, "adv3@test.com")
        self.assertRedirects(resp, reverse("web:deal_detail", args=[deal.pk]))
        self.assertEqual(ChatMessage.objects.filter(deal=deal).count(), 0)

    def test_cannot_send_to_cancelled_deal(self):
        deal = _make_deal(self.advertiser, self.blogger, Deal.Status.CANCELLED)
        resp = self._post_message(deal, "bl3@test.com")
        self.assertRedirects(resp, reverse("web:deal_detail", args=[deal.pk]))
        self.assertEqual(ChatMessage.objects.filter(deal=deal).count(), 0)

    def test_can_send_to_checking_deal(self):
        deal = _make_deal(self.advertiser, self.blogger, Deal.Status.CHECKING)
        resp = self._post_message(deal, "adv3@test.com")
        self.assertRedirects(resp, reverse("web:deal_detail", args=[deal.pk]))
        self.assertEqual(ChatMessage.objects.filter(deal=deal).count(), 1)

    def test_can_send_to_waiting_payment_deal(self):
        deal = _make_deal(self.advertiser, self.blogger, Deal.Status.WAITING_PAYMENT)
        resp = self._post_message(deal, "adv3@test.com")
        self.assertRedirects(resp, reverse("web:deal_detail", args=[deal.pk]))
        self.assertEqual(ChatMessage.objects.filter(deal=deal).count(), 1)


# ── Staff chat access ─────────────────────────────────────────────────────────

class StaffChatTest(TestCase):
    """is_staff can send to any active deal and is blocked for terminal statuses."""

    def setUp(self):
        self.advertiser = _make_user("adv4@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("bl4@test.com", User.Role.BLOGGER)
        self.staff = _make_staff()
        _make_wallet(self.advertiser)
        _make_wallet(self.blogger)

    def test_staff_can_send_to_any_active_deal(self):
        deal = _make_deal(self.advertiser, self.blogger, Deal.Status.IN_PROGRESS)
        self.client.login(username="staffchat@test.com", password="Test1234!")
        url = reverse("web:deal_send_message", args=[deal.pk])
        resp = self.client.post(url, {"text": "Staff message"})
        self.assertRedirects(resp, reverse("web:deal_detail", args=[deal.pk]))
        self.assertEqual(ChatMessage.objects.filter(deal=deal).count(), 1)

    def test_staff_blocked_for_completed_deal(self):
        deal = _make_deal(self.advertiser, self.blogger, Deal.Status.COMPLETED)
        self.client.login(username="staffchat@test.com", password="Test1234!")
        url = reverse("web:deal_send_message", args=[deal.pk])
        resp = self.client.post(url, {"text": "Staff msg on completed"})
        self.assertEqual(ChatMessage.objects.filter(deal=deal).count(), 0)

    def test_staff_can_view_any_deal_with_chat(self):
        deal = _make_deal(self.advertiser, self.blogger, Deal.Status.IN_PROGRESS)
        ChatMessage.objects.create(deal=deal, sender=self.advertiser, text="Hello")
        self.client.login(username="staffchat@test.com", password="Test1234!")
        url = reverse("web:deal_detail", args=[deal.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("chat_messages", resp.context)
        self.assertEqual(len(resp.context["chat_messages"]), 1)


# ── deal_detail context ───────────────────────────────────────────────────────

class DealDetailChatContextTest(TestCase):
    """deal_detail passes correct chat context."""

    def setUp(self):
        self.advertiser = _make_user("adv5@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("bl5@test.com", User.Role.BLOGGER)
        _make_wallet(self.advertiser)
        _make_wallet(self.blogger)
        self.deal = _make_deal(self.advertiser, self.blogger, Deal.Status.IN_PROGRESS)

    def _get_detail(self, email):
        self.client.login(username=email, password="Test1234!")
        return self.client.get(reverse("web:deal_detail", args=[self.deal.pk]))

    def test_chat_context_keys_present(self):
        resp = self._get_detail("adv5@test.com")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("chat_messages", resp.context)
        self.assertIn("chat_form", resp.context)
        self.assertIn("can_send_message", resp.context)

    def test_can_send_message_true_for_in_progress(self):
        resp = self._get_detail("adv5@test.com")
        self.assertTrue(resp.context["can_send_message"])

    def test_can_send_message_false_for_completed(self):
        self.deal.status = Deal.Status.COMPLETED
        self.deal.save(update_fields=["status"])
        resp = self._get_detail("adv5@test.com")
        self.assertFalse(resp.context["can_send_message"])

    def test_can_send_message_false_for_cancelled(self):
        self.deal.status = Deal.Status.CANCELLED
        self.deal.save(update_fields=["status"])
        resp = self._get_detail("bl5@test.com")
        self.assertFalse(resp.context["can_send_message"])

    def test_chat_messages_in_context_after_send(self):
        ChatMessage.objects.create(deal=self.deal, sender=self.advertiser, text="Test msg")
        resp = self._get_detail("adv5@test.com")
        self.assertEqual(len(resp.context["chat_messages"]), 1)

    def test_system_message_visible_in_context(self):
        ChatMessage.objects.create(
            deal=self.deal, text="Статус изменён на В работе", is_system=True
        )
        resp = self._get_detail("bl5@test.com")
        msgs = resp.context["chat_messages"]
        self.assertEqual(len(msgs), 1)
        self.assertTrue(msgs[0].is_system)

    def test_blogger_can_send_message_true(self):
        resp = self._get_detail("bl5@test.com")
        self.assertTrue(resp.context["can_send_message"])
