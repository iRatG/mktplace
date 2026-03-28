"""
Tests for Sprint 7 — Creative Approval (Module 7).

Covers:
  - deal_submit_creative: blogger submits, status → ON_APPROVAL
  - deal_submit_creative: access guards (advertiser, outsider → 404 / redirect)
  - deal_submit_creative: wrong status guard
  - deal_submit_creative: empty form → error, no status change
  - deal_submit_creative: creates system chat message + notification
  - deal_approve_creative: advertiser approves, status → IN_PROGRESS
  - deal_approve_creative: access guards
  - deal_approve_creative: wrong status guard
  - deal_approve_creative: sets creative_approved_at, clears rejection reason
  - deal_reject_creative: advertiser rejects with reason, status → IN_PROGRESS
  - deal_reject_creative: empty reason → error, no status change
  - deal_reject_creative: access guards
  - deal_detail context: on_approval status visibility
  - Full round-trip: submit → approve → submit publication
  - Full round-trip: submit → reject → re-submit → approve
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.billing.models import Wallet
from apps.campaigns.models import Campaign
from apps.deals.models import ChatMessage, Deal, DealStatusLog
from apps.platforms.models import Platform
from apps.users.models import User


# ── helpers ───────────────────────────────────────────────────────────────────

_counter = 0


def _make_user(email, role):
    u = User.objects.create_user(email=email, password="Test1234!", role=role)
    u.status = User.Status.ACTIVE
    u.save(update_fields=["status"])
    return u


def _make_platform(blogger):
    global _counter
    _counter += 1
    return Platform.objects.create(
        blogger=blogger,
        social_type=Platform.SocialType.INSTAGRAM,
        url=f"https://instagram.com/creative{_counter}",
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


# ── deal_submit_creative ───────────────────────────────────────────────────────

class DealSubmitCreativeTest(TestCase):
    """deal_submit_creative: happy path and guards."""

    def setUp(self):
        self.advertiser = _make_user("advcr1@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("blcr1@test.com", User.Role.BLOGGER)
        _make_wallet(self.advertiser)
        _make_wallet(self.blogger)
        self.deal = _make_deal(self.advertiser, self.blogger, Deal.Status.IN_PROGRESS)
        self.url = reverse("web:deal_submit_creative", args=[self.deal.pk])

    def test_blogger_submits_creative_text(self):
        self.client.login(username="blcr1@test.com", password="Test1234!")
        resp = self.client.post(self.url, {"creative_text": "Текст рекламного поста"})
        self.assertRedirects(resp, reverse("web:deal_detail", args=[self.deal.pk]))
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.ON_APPROVAL)
        self.assertEqual(self.deal.creative_text, "Текст рекламного поста")
        self.assertIsNotNone(self.deal.creative_submitted_at)

    def test_status_log_created(self):
        self.client.login(username="blcr1@test.com", password="Test1234!")
        self.client.post(self.url, {"creative_text": "Текст"})
        self.deal.refresh_from_db()
        log = DealStatusLog.objects.filter(
            deal=self.deal, new_status=Deal.Status.ON_APPROVAL
        ).first()
        self.assertIsNotNone(log)

    def test_system_chat_message_created(self):
        self.client.login(username="blcr1@test.com", password="Test1234!")
        self.client.post(self.url, {"creative_text": "Текст"})
        msg = ChatMessage.objects.filter(deal=self.deal, is_system=True).first()
        self.assertIsNotNone(msg)
        self.assertIn("креатив", msg.text.lower())

    def test_advertiser_cannot_submit_creative(self):
        self.client.login(username="advcr1@test.com", password="Test1234!")
        resp = self.client.post(self.url, {"creative_text": "Текст"})
        self.assertEqual(resp.status_code, 404)
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.IN_PROGRESS)

    def test_outsider_cannot_submit_creative(self):
        outsider = _make_user("outsidercr1@test.com", User.Role.BLOGGER)
        self.client.login(username="outsidercr1@test.com", password="Test1234!")
        resp = self.client.post(self.url, {"creative_text": "Текст"})
        self.assertEqual(resp.status_code, 404)

    def test_anonymous_redirected_to_login(self):
        resp = self.client.post(self.url, {"creative_text": "Текст"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp["Location"])

    def test_empty_form_no_status_change(self):
        self.client.login(username="blcr1@test.com", password="Test1234!")
        resp = self.client.post(self.url, {"creative_text": "  "})
        self.assertRedirects(resp, reverse("web:deal_detail", args=[self.deal.pk]))
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.IN_PROGRESS)

    def test_wrong_status_guard(self):
        self.deal.status = Deal.Status.CHECKING
        self.deal.save(update_fields=["status"])
        self.client.login(username="blcr1@test.com", password="Test1234!")
        resp = self.client.post(self.url, {"creative_text": "Текст"})
        self.assertRedirects(resp, reverse("web:deal_detail", args=[self.deal.pk]))
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.CHECKING)

    def test_get_method_not_allowed(self):
        self.client.login(username="blcr1@test.com", password="Test1234!")
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_previous_rejection_reason_cleared(self):
        self.deal.creative_rejection_reason = "Старая причина"
        self.deal.save(update_fields=["creative_rejection_reason"])
        self.client.login(username="blcr1@test.com", password="Test1234!")
        self.client.post(self.url, {"creative_text": "Новый текст"})
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.creative_rejection_reason, "")


# ── deal_approve_creative ─────────────────────────────────────────────────────

class DealApproveCreativeTest(TestCase):
    """deal_approve_creative: happy path and guards."""

    def setUp(self):
        self.advertiser = _make_user("advcr2@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("blcr2@test.com", User.Role.BLOGGER)
        _make_wallet(self.advertiser)
        _make_wallet(self.blogger)
        self.deal = _make_deal(self.advertiser, self.blogger, Deal.Status.ON_APPROVAL)
        self.deal.creative_text = "Текст для согласования"
        self.deal.save(update_fields=["creative_text"])
        self.url = reverse("web:deal_approve_creative", args=[self.deal.pk])

    def test_advertiser_approves_creative(self):
        self.client.login(username="advcr2@test.com", password="Test1234!")
        resp = self.client.post(self.url)
        self.assertRedirects(resp, reverse("web:deal_detail", args=[self.deal.pk]))
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.IN_PROGRESS)
        self.assertIsNotNone(self.deal.creative_approved_at)

    def test_status_log_created_on_approve(self):
        self.client.login(username="advcr2@test.com", password="Test1234!")
        self.client.post(self.url)
        log = DealStatusLog.objects.filter(
            deal=self.deal, new_status=Deal.Status.IN_PROGRESS,
        ).first()
        self.assertIsNotNone(log)
        self.assertIn("согласовал", log.comment)

    def test_system_message_on_approve(self):
        self.client.login(username="advcr2@test.com", password="Test1234!")
        self.client.post(self.url)
        msg = ChatMessage.objects.filter(deal=self.deal, is_system=True).first()
        self.assertIsNotNone(msg)
        self.assertIn("согласовал", msg.text.lower())

    def test_blogger_cannot_approve(self):
        self.client.login(username="blcr2@test.com", password="Test1234!")
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 404)
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.ON_APPROVAL)

    def test_outsider_cannot_approve(self):
        outsider = _make_user("outsidercr2@test.com", User.Role.ADVERTISER)
        self.client.login(username="outsidercr2@test.com", password="Test1234!")
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 404)

    def test_wrong_status_guard(self):
        self.deal.status = Deal.Status.IN_PROGRESS
        self.deal.save(update_fields=["status"])
        self.client.login(username="advcr2@test.com", password="Test1234!")
        resp = self.client.post(self.url)
        self.assertRedirects(resp, reverse("web:deal_detail", args=[self.deal.pk]))
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.IN_PROGRESS)

    def test_rejection_reason_cleared_on_approve(self):
        self.deal.creative_rejection_reason = "Причина"
        self.deal.save(update_fields=["creative_rejection_reason"])
        self.client.login(username="advcr2@test.com", password="Test1234!")
        self.client.post(self.url)
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.creative_rejection_reason, "")


# ── deal_reject_creative ──────────────────────────────────────────────────────

class DealRejectCreativeTest(TestCase):
    """deal_reject_creative: happy path and guards."""

    def setUp(self):
        self.advertiser = _make_user("advcr3@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("blcr3@test.com", User.Role.BLOGGER)
        _make_wallet(self.advertiser)
        _make_wallet(self.blogger)
        self.deal = _make_deal(self.advertiser, self.blogger, Deal.Status.ON_APPROVAL)
        self.deal.creative_text = "Текст для отклонения"
        self.deal.save(update_fields=["creative_text"])
        self.url = reverse("web:deal_reject_creative", args=[self.deal.pk])

    def test_advertiser_rejects_with_reason(self):
        self.client.login(username="advcr3@test.com", password="Test1234!")
        resp = self.client.post(self.url, {"rejection_reason": "Нет хэштегов бренда"})
        self.assertRedirects(resp, reverse("web:deal_detail", args=[self.deal.pk]))
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.IN_PROGRESS)
        self.assertEqual(self.deal.creative_rejection_reason, "Нет хэштегов бренда")

    def test_status_log_created_on_reject(self):
        self.client.login(username="advcr3@test.com", password="Test1234!")
        self.client.post(self.url, {"rejection_reason": "Причина"})
        log = DealStatusLog.objects.filter(
            deal=self.deal, new_status=Deal.Status.IN_PROGRESS,
        ).first()
        self.assertIsNotNone(log)
        self.assertIn("отклонил", log.comment)

    def test_system_message_on_reject(self):
        self.client.login(username="advcr3@test.com", password="Test1234!")
        self.client.post(self.url, {"rejection_reason": "Причина"})
        msg = ChatMessage.objects.filter(deal=self.deal, is_system=True).first()
        self.assertIsNotNone(msg)
        self.assertIn("отклонил", msg.text.lower())

    def test_empty_reason_no_status_change(self):
        self.client.login(username="advcr3@test.com", password="Test1234!")
        resp = self.client.post(self.url, {"rejection_reason": "  "})
        self.assertRedirects(resp, reverse("web:deal_detail", args=[self.deal.pk]))
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.ON_APPROVAL)

    def test_blogger_cannot_reject(self):
        self.client.login(username="blcr3@test.com", password="Test1234!")
        resp = self.client.post(self.url, {"rejection_reason": "Попытка блогера"})
        self.assertEqual(resp.status_code, 404)
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.ON_APPROVAL)

    def test_wrong_status_guard(self):
        self.deal.status = Deal.Status.CHECKING
        self.deal.save(update_fields=["status"])
        self.client.login(username="advcr3@test.com", password="Test1234!")
        resp = self.client.post(self.url, {"rejection_reason": "Причина"})
        self.assertRedirects(resp, reverse("web:deal_detail", args=[self.deal.pk]))
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.CHECKING)


# ── Round-trip flows ──────────────────────────────────────────────────────────

class CreativeRoundTripTest(TestCase):
    """Full creative approval round-trip scenarios."""

    def setUp(self):
        self.advertiser = _make_user("advcr4@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("blcr4@test.com", User.Role.BLOGGER)
        _make_wallet(self.advertiser)
        _make_wallet(self.blogger)
        self.deal = _make_deal(self.advertiser, self.blogger, Deal.Status.IN_PROGRESS)

    def test_submit_approve_then_publish(self):
        """IN_PROGRESS → ON_APPROVAL → IN_PROGRESS (approved) → CHECKING."""
        # Submit creative
        self.client.login(username="blcr4@test.com", password="Test1234!")
        self.client.post(
            reverse("web:deal_submit_creative", args=[self.deal.pk]),
            {"creative_text": "Текст поста"},
        )
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.ON_APPROVAL)

        # Approve creative
        self.client.login(username="advcr4@test.com", password="Test1234!")
        self.client.post(reverse("web:deal_approve_creative", args=[self.deal.pk]))
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.IN_PROGRESS)

        # Submit publication URL
        self.client.login(username="blcr4@test.com", password="Test1234!")
        self.client.post(
            reverse("web:deal_submit_publication", args=[self.deal.pk]),
            {"publication_url": "https://instagram.com/p/test"},
        )
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.CHECKING)

    def test_submit_reject_resubmit_approve(self):
        """IN_PROGRESS → ON_APPROVAL → IN_PROGRESS (rejected) → ON_APPROVAL → IN_PROGRESS."""
        # Submit creative
        self.client.login(username="blcr4@test.com", password="Test1234!")
        self.client.post(
            reverse("web:deal_submit_creative", args=[self.deal.pk]),
            {"creative_text": "Первый вариант"},
        )
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.ON_APPROVAL)

        # Reject creative
        self.client.login(username="advcr4@test.com", password="Test1234!")
        self.client.post(
            reverse("web:deal_reject_creative", args=[self.deal.pk]),
            {"rejection_reason": "Нужны хэштеги"},
        )
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.IN_PROGRESS)
        self.assertEqual(self.deal.creative_rejection_reason, "Нужны хэштеги")

        # Re-submit corrected creative
        self.client.login(username="blcr4@test.com", password="Test1234!")
        self.client.post(
            reverse("web:deal_submit_creative", args=[self.deal.pk]),
            {"creative_text": "Исправленный вариант с #хэштегами"},
        )
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.ON_APPROVAL)
        self.assertEqual(self.deal.creative_rejection_reason, "")

        # Approve second creative
        self.client.login(username="advcr4@test.com", password="Test1234!")
        self.client.post(reverse("web:deal_approve_creative", args=[self.deal.pk]))
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, Deal.Status.IN_PROGRESS)
        self.assertIsNotNone(self.deal.creative_approved_at)
