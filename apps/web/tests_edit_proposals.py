"""
Правки кампании модератором с подтверждением рекламодателем (issue #6,
openspec change add-moderator-edit-proposals).
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.campaigns.models import Campaign, CampaignEditProposal
from apps.deals.models import Deal
from apps.notifications.models import Notification
from apps.platforms.models import Platform
from apps.web.campaign_proposals import form_data_from_campaign

User = get_user_model()
_n = 0


def _user(role, is_staff=False):
    global _n
    _n += 1
    user = User.objects.create_user(email=f"prop{_n}@test.com", password="pass1234", role=role)
    user.status = User.Status.ACTIVE
    user.is_email_confirmed = True
    user.is_staff = is_staff
    user.save(update_fields=["status", "is_email_confirmed", "is_staff"])
    return user


def _campaign(advertiser, status=Campaign.Status.MODERATION, **extra):
    today = timezone.now().date()
    fields = dict(
        advertiser=advertiser, name="Кампания", payment_type=Campaign.PaymentType.FIXED,
        fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=status,
        start_date=today + timedelta(days=1), end_date=today + timedelta(days=30),
        deadline=today + timedelta(days=20), content_types=["post"], allowed_socials=["instagram"],
    )
    fields.update(extra)
    return Campaign.objects.create(**fields)


def _post_data(campaign, comment="Поправил цену и сроки", **changes):
    data = form_data_from_campaign(campaign)
    payload = {k: data.getlist(k) for k in data}
    for key, value in changes.items():
        payload[key] = value
    payload["proposal_comment"] = comment
    return payload


class ModeratorProposesTest(TestCase):
    def setUp(self):
        self.staff = _user(User.Role.ADVERTISER, is_staff=True)
        self.adv = _user(User.Role.ADVERTISER)
        self.c = _campaign(self.adv)
        self.client.force_login(self.staff)
        self.url = reverse("web:admin_campaign_propose", args=[self.c.pk])
        self.new_end = (timezone.now().date() + timedelta(days=40)).isoformat()

    def test_form_prefilled(self):
        r = self.client.get(self.url)
        self.assertContains(r, "Правки модератора")
        self.assertContains(r, 'name="proposal_comment"')
        self.assertContains(r, "150000")

    def test_propose_creates_pending_and_notifies(self):
        r = self.client.post(self.url, _post_data(self.c, fixed_price="120 000", end_date=self.new_end))
        self.assertRedirects(r, reverse("web:admin_campaign_detail", args=[self.c.pk]))
        p = CampaignEditProposal.objects.get(campaign=self.c)
        self.assertEqual(p.status, CampaignEditProposal.Status.PENDING)
        self.assertEqual(set(p.changes), {"fixed_price", "end_date"})
        self.c.refresh_from_db()
        self.assertEqual(self.c.status, Campaign.Status.MODERATION)
        self.assertEqual(self.c.fixed_price, Decimal("150000"))
        n = Notification.objects.get(user=self.adv, type=Notification.Type.CAMPAIGN_CHANGES_PROPOSED)
        self.assertEqual(n.target_url, reverse("web:campaign_detail", args=[self.c.pk]))

    def test_invalid_values_rejected(self):
        r = self.client.post(self.url, _post_data(self.c, fixed_price="2 000 000"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "не может быть больше бюджета")
        self.assertFalse(CampaignEditProposal.objects.exists())

    def test_no_changes_rejected(self):
        self.client.post(self.url, _post_data(self.c))
        self.assertFalse(CampaignEditProposal.objects.exists())

    def test_comment_required(self):
        self.client.post(self.url, _post_data(self.c, comment="", fixed_price="120000"))
        self.assertFalse(CampaignEditProposal.objects.exists())

    def test_non_staff_denied(self):
        self.client.force_login(self.adv)
        self.client.post(self.url, _post_data(self.c, fixed_price="120000"))
        self.assertFalse(CampaignEditProposal.objects.exists())

    def test_only_for_moderation(self):
        active = _campaign(self.adv, status=Campaign.Status.ACTIVE)
        self.client.post(reverse("web:admin_campaign_propose", args=[active.pk]),
                         _post_data(active, fixed_price="120000"))
        self.assertFalse(CampaignEditProposal.objects.exists())

    def test_one_pending_per_campaign(self):
        CampaignEditProposal.objects.create(campaign=self.c, author=self.staff, changes={}, comment="x")
        with self.assertRaises(IntegrityError):
            CampaignEditProposal.objects.create(campaign=self.c, author=self.staff, changes={}, comment="y")


class PendingProposalBlocksModerationTest(TestCase):
    def setUp(self):
        self.staff = _user(User.Role.ADVERTISER, is_staff=True)
        self.adv = _user(User.Role.ADVERTISER)
        self.c = _campaign(self.adv)
        CampaignEditProposal.objects.create(
            campaign=self.c, author=self.staff, comment="Исправьте цену",
            changes={"fixed_price": {"old": "150000", "new": "120000"}},
        )
        self.client.force_login(self.staff)

    def test_approve_and_reject_blocked(self):
        self.client.post(reverse("web:admin_campaign_approve", args=[self.c.pk]))
        self.client.post(reverse("web:admin_campaign_reject", args=[self.c.pk]), {"reason": "нет"})
        self.c.refresh_from_db()
        self.assertEqual(self.c.status, Campaign.Status.MODERATION)

    def test_card_shows_proposal(self):
        r = self.client.get(reverse("web:admin_campaign_detail", args=[self.c.pk]))
        self.assertContains(r, "Ждём ответа рекламодателя")
        self.assertContains(r, "Цена за размещение")
        self.assertContains(r, "120 000")
        self.assertNotContains(r, reverse("web:admin_campaign_approve", args=[self.c.pk]))

    def test_queue_marks_campaign(self):
        r = self.client.get(reverse("web:admin_campaigns"))
        self.assertContains(r, "ждём ответа рекламодателя на правки")


class AdvertiserAnswersTest(TestCase):
    def setUp(self):
        self.staff = _user(User.Role.ADVERTISER, is_staff=True)
        self.adv = _user(User.Role.ADVERTISER)
        self.c = _campaign(self.adv, rejection_reason="старая причина")
        self.new_end = (timezone.now().date() + timedelta(days=40)).isoformat()
        self.p = CampaignEditProposal.objects.create(
            campaign=self.c, author=self.staff, comment="Снизьте цену",
            changes={
                "fixed_price": {"old": "150000", "new": "120000"},
                "end_date": {"old": self.c.end_date.isoformat(), "new": self.new_end},
            },
        )
        self.client.force_login(self.adv)

    def test_owner_sees_changes_and_buttons(self):
        r = self.client.get(reverse("web:campaign_detail", args=[self.c.pk]))
        self.assertContains(r, "Модератор предложил правки")
        self.assertContains(r, "Снизьте цену")
        self.assertContains(r, "120 000")
        self.assertContains(r, reverse("web:campaign_proposal_accept", args=[self.c.pk]))

    def test_blogger_cannot_see_campaign(self):
        self.client.force_login(_user(User.Role.BLOGGER))
        r = self.client.get(reverse("web:campaign_detail", args=[self.c.pk]))
        self.assertEqual(r.status_code, 404)

    def test_accept_applies_and_activates(self):
        self.client.post(reverse("web:campaign_proposal_accept", args=[self.c.pk]))
        self.c.refresh_from_db()
        self.p.refresh_from_db()
        self.assertEqual(self.c.status, Campaign.Status.ACTIVE)
        self.assertEqual(self.c.fixed_price, Decimal("120000"))
        self.assertEqual(self.c.end_date.isoformat(), self.new_end)
        self.assertEqual(self.c.rejection_reason, "")
        self.assertEqual(self.p.status, CampaignEditProposal.Status.ACCEPTED)
        self.assertIsNotNone(self.p.responded_at)
        n = Notification.objects.get(user=self.staff, type=Notification.Type.CAMPAIGN_CHANGES_ACCEPTED)
        self.assertEqual(n.target_url, reverse("web:admin_campaign_detail", args=[self.c.pk]))

    def test_decline_rejects_with_comment(self):
        self.client.post(reverse("web:campaign_proposal_decline", args=[self.c.pk]))
        self.c.refresh_from_db()
        self.p.refresh_from_db()
        self.assertEqual(self.c.status, Campaign.Status.REJECTED)
        self.assertEqual(self.c.rejection_reason, "Снизьте цену")
        self.assertEqual(self.c.fixed_price, Decimal("150000"))
        self.assertEqual(self.p.status, CampaignEditProposal.Status.DECLINED)
        self.assertTrue(Notification.objects.filter(
            user=self.staff, type=Notification.Type.CAMPAIGN_CHANGES_DECLINED).exists())

    def test_second_answer_ignored(self):
        self.client.post(reverse("web:campaign_proposal_decline", args=[self.c.pk]))
        self.client.post(reverse("web:campaign_proposal_accept", args=[self.c.pk]))
        self.c.refresh_from_db()
        self.assertEqual(self.c.status, Campaign.Status.REJECTED)
        self.assertEqual(self.c.fixed_price, Decimal("150000"))

    def test_other_user_cannot_answer(self):
        self.client.force_login(_user(User.Role.ADVERTISER))
        r = self.client.post(reverse("web:campaign_proposal_accept", args=[self.c.pk]))
        self.assertEqual(r.status_code, 404)
        self.p.refresh_from_db()
        self.assertEqual(self.p.status, CampaignEditProposal.Status.PENDING)

    def test_stale_values_not_applied(self):
        self.p.changes = {"max_bloggers": {"old": "0", "new": "1"}}
        self.p.save(update_fields=["changes"])
        for i in range(2):
            blogger = _user(User.Role.BLOGGER)
            platform = Platform.objects.create(
                blogger=blogger, social_type=Platform.SocialType.INSTAGRAM,
                url=f"https://instagram.com/stale{i}{blogger.pk}", status=Platform.Status.APPROVED,
            )
            Deal.objects.create(campaign=self.c, blogger=blogger, platform=platform, advertiser=self.adv,
                                amount=Decimal("150000"), status=Deal.Status.IN_PROGRESS)
        self.client.post(reverse("web:campaign_proposal_accept", args=[self.c.pk]))
        self.c.refresh_from_db()
        self.p.refresh_from_db()
        self.assertEqual(self.c.status, Campaign.Status.MODERATION)
        self.assertEqual(self.c.max_bloggers, 0)
        self.assertEqual(self.p.status, CampaignEditProposal.Status.PENDING)
