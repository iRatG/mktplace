"""
Tests for Sprint 8 — CPA Model (Module 9).

Covers:
  - TrackingLink created lazily in deal_detail for CPA deals
  - TrackingLink NOT created for non-CPA deals
  - cpa_click_track: click logged, redirect to target URL with click_id
  - cpa_click_track: inactive link → 404
  - cpa_click_track: unknown slug → 404
  - cpa_click_track: CLICK type — auto-conversion + billing
  - cpa_click_track: CLICK type, insufficient funds — conversion uncredited
  - cpa_postback: lead conversion credited
  - cpa_postback: missing click_id → 400
  - cpa_postback: invalid click_id → 400
  - cpa_postback: unknown click_id → 404
  - cpa_postback: idempotent — second postback ignored
  - cpa_postback: no cpa_rate on campaign → 400
  - BillingService.credit_cpa_conversion: debits advertiser, credits blogger
  - BillingService.credit_cpa_conversion: already credited → ValueError
  - BillingService.credit_cpa_conversion: insufficient advertiser funds → ValueError
  - TrackingLink slug uniqueness
  - Full flow: CPA deal → click → postback → balance credited
"""

import uuid
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.billing.models import Transaction, Wallet
from apps.billing.services import BillingService
from apps.campaigns.models import Campaign
from apps.deals.models import ClickLog, Conversion, Deal, TrackingLink
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
        url=f"https://instagram.com/cpa{_counter}",
        subscribers=5000,
        avg_views=300,
        engagement_rate=Decimal("3.00"),
        price_post=Decimal("30000"),
        status=Platform.Status.APPROVED,
    )


def _make_campaign(advertiser, payment_type=Campaign.PaymentType.CPA,
                   cpa_type=Campaign.CPAType.CLICK, cpa_rate=Decimal("1000"),
                   cpa_tracking_url="https://example.com/landing"):
    global _counter
    _counter += 1
    return Campaign.objects.create(
        advertiser=advertiser,
        name=f"CPA Campaign {_counter}",
        description="desc",
        payment_type=payment_type,
        fixed_price=Decimal("0"),
        budget=Decimal("100000"),
        status=Campaign.Status.ACTIVE,
        cpa_type=cpa_type,
        cpa_rate=cpa_rate,
        cpa_tracking_url=cpa_tracking_url,
    )


def _make_deal(advertiser, blogger, campaign=None, status=Deal.Status.IN_PROGRESS):
    if campaign is None:
        campaign = _make_campaign(advertiser)
    platform = _make_platform(blogger)
    return Deal.objects.create(
        campaign=campaign,
        blogger=blogger,
        platform=platform,
        advertiser=advertiser,
        amount=Decimal("0"),  # CPA deals have amount=0 (paid per conversion)
        status=status,
    )


def _make_wallet(user, available=Decimal("50000")):
    w, _ = Wallet.objects.get_or_create(user=user)
    w.available_balance = available
    w.save(update_fields=["available_balance"])
    return w


def _make_tracking_link(deal):
    return TrackingLink.objects.create(deal=deal)


# ── TrackingLink model ─────────────────────────────────────────────────────────

class TrackingLinkModelTest(TestCase):
    """TrackingLink model: slug generation and uniqueness."""

    def setUp(self):
        self.advertiser = _make_user("advcpa_m@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("blcpa_m@test.com", User.Role.BLOGGER)

    def test_slug_auto_generated(self):
        deal = _make_deal(self.advertiser, self.blogger)
        tl = TrackingLink.objects.create(deal=deal)
        self.assertEqual(len(tl.slug), 16)
        self.assertTrue(tl.is_active)

    def test_slug_unique(self):
        deal1 = _make_deal(self.advertiser, self.blogger)
        deal2 = _make_deal(self.advertiser, self.blogger)
        tl1 = TrackingLink.objects.create(deal=deal1)
        tl2 = TrackingLink.objects.create(deal=deal2)
        self.assertNotEqual(tl1.slug, tl2.slug)

    def test_one_tracking_link_per_deal(self):
        deal = _make_deal(self.advertiser, self.blogger)
        TrackingLink.objects.create(deal=deal)
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            TrackingLink.objects.create(deal=deal)


# ── deal_detail: TrackingLink context ─────────────────────────────────────────

class DealDetailCPAContextTest(TestCase):
    """deal_detail: tracking_link in context for CPA deals only."""

    def setUp(self):
        self.advertiser = _make_user("advcpa_ctx@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("blcpa_ctx@test.com", User.Role.BLOGGER)
        _make_wallet(self.advertiser)
        _make_wallet(self.blogger)

    def test_tracking_link_created_for_cpa_deal(self):
        deal = _make_deal(self.advertiser, self.blogger)
        self.client.login(username="blcpa_ctx@test.com", password="Test1234!")
        resp = self.client.get(reverse("web:deal_detail", args=[deal.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.context["tracking_link"])
        self.assertTrue(TrackingLink.objects.filter(deal=deal).exists())

    def test_tracking_link_not_in_context_for_fixed_deal(self):
        campaign = _make_campaign(self.advertiser, payment_type=Campaign.PaymentType.FIXED)
        platform = _make_platform(self.blogger)
        deal = Deal.objects.create(
            campaign=campaign, blogger=self.blogger, platform=platform,
            advertiser=self.advertiser, amount=Decimal("30000"),
            status=Deal.Status.IN_PROGRESS,
        )
        self.client.login(username="blcpa_ctx@test.com", password="Test1234!")
        resp = self.client.get(reverse("web:deal_detail", args=[deal.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context["tracking_link"])

    def test_tracking_link_idempotent_on_repeated_page_load(self):
        deal = _make_deal(self.advertiser, self.blogger)
        self.client.login(username="blcpa_ctx@test.com", password="Test1234!")
        self.client.get(reverse("web:deal_detail", args=[deal.pk]))
        self.client.get(reverse("web:deal_detail", args=[deal.pk]))
        # Only one TrackingLink per deal
        self.assertEqual(TrackingLink.objects.filter(deal=deal).count(), 1)


# ── cpa_click_track ────────────────────────────────────────────────────────────

class CPAClickTrackTest(TestCase):
    """cpa_click_track: click logging and redirects."""

    def setUp(self):
        self.advertiser = _make_user("advcpa_click@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("blcpa_click@test.com", User.Role.BLOGGER)
        _make_wallet(self.advertiser, Decimal("50000"))
        _make_wallet(self.blogger)
        self.deal = _make_deal(self.advertiser, self.blogger)
        self.tl = _make_tracking_link(self.deal)

    def test_click_logged(self):
        resp = self.client.get(reverse("web:cpa_click_track", args=[self.tl.slug]))
        self.assertEqual(ClickLog.objects.filter(tracking_link=self.tl).count(), 1)

    def test_redirect_to_target_url(self):
        resp = self.client.get(reverse("web:cpa_click_track", args=[self.tl.slug]))
        # Should redirect (302) to target URL
        self.assertEqual(resp.status_code, 302)
        target = self.deal.campaign.cpa_tracking_url
        self.assertIn(target, resp["Location"])

    def test_click_id_appended_to_redirect(self):
        resp = self.client.get(reverse("web:cpa_click_track", args=[self.tl.slug]))
        click = ClickLog.objects.filter(tracking_link=self.tl).first()
        self.assertIn(str(click.click_id), resp["Location"])

    def test_unknown_slug_404(self):
        resp = self.client.get(reverse("web:cpa_click_track", args=["0000000000000000"]))
        self.assertEqual(resp.status_code, 404)

    def test_inactive_link_404(self):
        self.tl.is_active = False
        self.tl.save()
        resp = self.client.get(reverse("web:cpa_click_track", args=[self.tl.slug]))
        self.assertEqual(resp.status_code, 404)

    def test_no_login_required(self):
        # Public endpoint — anonymous user should work
        resp = self.client.get(reverse("web:cpa_click_track", args=[self.tl.slug]))
        self.assertNotEqual(resp.status_code, 403)
        self.assertNotEqual(resp.status_code, 302 and resp["Location"].startswith("/login"))

    def test_click_type_auto_conversion(self):
        """CLICK type → Conversion created and credited immediately."""
        resp = self.client.get(reverse("web:cpa_click_track", args=[self.tl.slug]))
        self.assertEqual(Conversion.objects.filter(
            tracking_link=self.tl,
            conversion_type=Conversion.ConversionType.CLICK,
            credited=True,
        ).count(), 1)

    def test_click_type_insufficient_funds_stays_uncredited(self):
        """Advertiser has 0 balance — conversion created but not credited."""
        adv_wallet, _ = Wallet.objects.get_or_create(user=self.advertiser)
        adv_wallet.available_balance = Decimal("0")
        adv_wallet.save()

        self.client.get(reverse("web:cpa_click_track", args=[self.tl.slug]))
        # Conversion exists but uncredited
        self.assertEqual(Conversion.objects.filter(
            tracking_link=self.tl,
            conversion_type=Conversion.ConversionType.CLICK,
            credited=False,
        ).count(), 1)


# ── cpa_postback ───────────────────────────────────────────────────────────────

class CPAPostbackTest(TestCase):
    """cpa_postback: lead/sale/install postback flow."""

    def setUp(self):
        self.advertiser = _make_user("advcpa_pb@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("blcpa_pb@test.com", User.Role.BLOGGER)
        _make_wallet(self.advertiser, Decimal("50000"))
        _make_wallet(self.blogger)
        campaign = _make_campaign(
            self.advertiser,
            cpa_type=Campaign.CPAType.LEAD,
            cpa_rate=Decimal("2000"),
        )
        self.deal = _make_deal(self.advertiser, self.blogger, campaign=campaign)
        self.tl = _make_tracking_link(self.deal)
        # Create a click
        self.click = ClickLog.objects.create(tracking_link=self.tl)
        self.pb_url = reverse("web:cpa_postback")

    def test_lead_conversion_credited(self):
        resp = self.client.get(self.pb_url, {
            "click_id": str(self.click.click_id),
            "goal": "lead",
        })
        self.assertEqual(resp.status_code, 200)
        import json
        data = json.loads(resp.content)
        self.assertEqual(data["status"], "ok")
        self.assertTrue(Conversion.objects.filter(
            click_log=self.click,
            conversion_type=Conversion.ConversionType.LEAD,
            credited=True,
        ).exists())

    def test_missing_click_id_returns_400(self):
        resp = self.client.get(self.pb_url)
        self.assertEqual(resp.status_code, 400)

    def test_invalid_click_id_returns_400(self):
        resp = self.client.get(self.pb_url, {"click_id": "not-a-uuid"})
        self.assertEqual(resp.status_code, 400)

    def test_unknown_click_id_returns_404(self):
        resp = self.client.get(self.pb_url, {"click_id": str(uuid.uuid4())})
        self.assertEqual(resp.status_code, 404)

    def test_idempotent_second_postback_ignored(self):
        self.client.get(self.pb_url, {"click_id": str(self.click.click_id), "goal": "lead"})
        resp = self.client.get(self.pb_url, {"click_id": str(self.click.click_id), "goal": "lead"})
        import json
        data = json.loads(resp.content)
        self.assertEqual(data["detail"], "already credited")
        # Still only one conversion
        self.assertEqual(Conversion.objects.filter(click_log=self.click).count(), 1)

    def test_post_method_also_works(self):
        resp = self.client.post(self.pb_url, {
            "click_id": str(self.click.click_id),
            "goal": "lead",
        })
        import json
        data = json.loads(resp.content)
        self.assertEqual(data["status"], "ok")

    def test_no_cpa_rate_returns_400(self):
        campaign = _make_campaign(
            self.advertiser,
            cpa_type=Campaign.CPAType.LEAD,
            cpa_rate=None,
        )
        deal = _make_deal(self.advertiser, self.blogger, campaign=campaign)
        tl = _make_tracking_link(deal)
        click = ClickLog.objects.create(tracking_link=tl)
        resp = self.client.get(self.pb_url, {"click_id": str(click.click_id), "goal": "lead"})
        self.assertEqual(resp.status_code, 400)


# ── BillingService.credit_cpa_conversion ──────────────────────────────────────

class CreditCPAConversionTest(TestCase):
    """BillingService.credit_cpa_conversion: billing mechanics."""

    def setUp(self):
        self.advertiser = _make_user("advcpa_bill@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("blcpa_bill@test.com", User.Role.BLOGGER)
        self.adv_wallet = _make_wallet(self.advertiser, Decimal("50000"))
        self.bl_wallet = _make_wallet(self.blogger, Decimal("0"))
        campaign = _make_campaign(self.advertiser, cpa_rate=Decimal("1000"))
        self.deal = _make_deal(self.advertiser, self.blogger, campaign=campaign)
        self.tl = _make_tracking_link(self.deal)
        self.click = ClickLog.objects.create(tracking_link=self.tl)

    def _make_conversion(self, amount=Decimal("1000")):
        return Conversion.objects.create(
            tracking_link=self.tl,
            click_log=self.click,
            conversion_type=Conversion.ConversionType.CLICK,
            amount=amount,
        )

    def test_debits_advertiser(self):
        conv = self._make_conversion()
        BillingService.credit_cpa_conversion(conv)
        self.adv_wallet.refresh_from_db()
        self.assertEqual(self.adv_wallet.available_balance, Decimal("49000"))

    def test_credits_blogger_after_commission(self):
        conv = self._make_conversion()
        BillingService.credit_cpa_conversion(conv)
        self.bl_wallet.refresh_from_db()
        # 15% commission: blogger gets 850
        self.assertEqual(self.bl_wallet.available_balance, Decimal("850.00"))

    def test_marks_conversion_credited(self):
        conv = self._make_conversion()
        BillingService.credit_cpa_conversion(conv)
        conv.refresh_from_db()
        self.assertTrue(conv.credited)

    def test_already_credited_raises(self):
        conv = self._make_conversion()
        BillingService.credit_cpa_conversion(conv)
        with self.assertRaises(ValueError):
            BillingService.credit_cpa_conversion(conv)

    def test_insufficient_funds_raises(self):
        self.adv_wallet.available_balance = Decimal("0")
        self.adv_wallet.save()
        conv = self._make_conversion()
        with self.assertRaises(ValueError):
            BillingService.credit_cpa_conversion(conv)

    def test_transactions_created(self):
        conv = self._make_conversion()
        BillingService.credit_cpa_conversion(conv)
        self.assertEqual(
            Transaction.objects.filter(wallet=self.adv_wallet, type=Transaction.Type.PAYMENT).count(), 1
        )
        self.assertEqual(
            Transaction.objects.filter(wallet=self.bl_wallet, type=Transaction.Type.EARNING).count(), 1
        )


# ── Full CPA flow ─────────────────────────────────────────────────────────────

class CPAFullFlowTest(TestCase):
    """End-to-end CPA flow: deal → click → postback → balance updated."""

    def test_full_lead_flow(self):
        advertiser = _make_user("advcpa_full@test.com", User.Role.ADVERTISER)
        blogger = _make_user("blcpa_full@test.com", User.Role.BLOGGER)
        adv_wallet = _make_wallet(advertiser, Decimal("50000"))
        bl_wallet = _make_wallet(blogger, Decimal("0"))

        campaign = _make_campaign(
            advertiser,
            cpa_type=Campaign.CPAType.LEAD,
            cpa_rate=Decimal("2000"),
            cpa_tracking_url="https://example.com/product",
        )
        deal = _make_deal(advertiser, blogger, campaign=campaign)

        # Blogger opens deal page → TrackingLink created
        self.client.login(username="blcpa_full@test.com", password="Test1234!")
        self.client.get(reverse("web:deal_detail", args=[deal.pk]))
        tl = TrackingLink.objects.get(deal=deal)

        # Anonymous user clicks the link
        self.client.logout()
        resp = self.client.get(reverse("web:cpa_click_track", args=[tl.slug]))
        self.assertEqual(resp.status_code, 302)
        click = ClickLog.objects.get(tracking_link=tl)

        # Advertiser's system sends postback
        pb_url = reverse("web:cpa_postback")
        resp = self.client.get(pb_url, {
            "click_id": str(click.click_id),
            "goal": "lead",
        })
        self.assertEqual(resp.status_code, 200)

        # Verify balances
        adv_wallet.refresh_from_db()
        bl_wallet.refresh_from_db()
        self.assertEqual(adv_wallet.available_balance, Decimal("48000"))
        # Blogger earns 2000 * 0.85 = 1700
        self.assertEqual(bl_wallet.available_balance, Decimal("1700.00"))
