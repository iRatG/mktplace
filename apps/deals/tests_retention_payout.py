"""
#35: размещение и выплата по условиям оферты — доказательства исполнения, «Принять публикацию» без оплаты, выплата
по позднему из сроков (3 рабочих дня на претензию и минимальный срок сохранения); сделки до правила — 72 часа.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.billing.models import Wallet
from apps.campaigns.models import Campaign
from apps.campaigns.models import Response as CampaignResponse
from apps.campaigns.testing import CARD_FIELDS, deal_from_response
from apps.campaigns.validation import working_days_after
from apps.deals import services as t
from apps.deals.models import Deal, DealEvidence
from apps.deals.services import TransitionError
from apps.notifications.models import Notification
from apps.platforms.models import Platform
from apps.users.models import User

S = Deal.Status
_n = 0


def _file(name="s.png"):
    return SimpleUploadedFile(name, b"\x89PNG", content_type="image/png")


class RetentionPayoutTest(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.adv = User.objects.create_user(email="adv@test.com", password="pass1234", role=User.Role.ADVERTISER,
                                            status=User.Status.ACTIVE)
        Wallet.objects.filter(user=self.adv).update(available_balance=Decimal("1000000"))
        self.blogger = User.objects.create_user(email="bl@test.com", password="pass1234", role=User.Role.BLOGGER,
                                                status=User.Status.ACTIVE)
        self.campaign = Campaign.objects.create(
            advertiser=self.adv, name="Кампания", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=Campaign.Status.ACTIVE,
            start_date=self.today, end_date=self.today + timedelta(days=40), approval_required=False,
            min_retention_days=7, evidence_required=["screenshot"], **CARD_FIELDS,
        )

    def _deal(self, **campaign):
        global _n
        _n += 1
        if campaign:
            Campaign.objects.filter(pk=self.campaign.pk).update(**campaign)
            self.campaign.refresh_from_db()
        platform = Platform.objects.create(
            blogger=self.blogger, social_type=Platform.SocialType.INSTAGRAM, url=f"https://instagram.com/rp{_n}",
            subscribers=12000, price_post=Decimal("50000"), status=Platform.Status.APPROVED,
        )
        resp = CampaignResponse.objects.create(campaign=self.campaign, blogger=self.blogger, platform=platform,
                                               content_type="post", proposed_price=Decimal("150000"))
        deal = deal_from_response(resp, self.adv, publication_date=self.today)
        return deal

    def _publish(self, deal):
        t.submit_publication(deal.pk, self.blogger, "https://instagram.com/p/1", {"screenshot": [_file()]})
        deal.refresh_from_db()
        return deal

    def _age(self, deal, **delta):
        Deal.objects.filter(pk=deal.pk).update(publication_at=timezone.now() - timedelta(**delta))
        deal.refresh_from_db()
        return deal

    def _balance(self, user):
        return Wallet.objects.get(user=user).available_balance

    # ── Условия оферты в сделке ──────────────────────────────────────────────

    def test_terms_copied_and_evidence_required(self):
        deal = self._deal()
        self.assertEqual((deal.min_retention_days, deal.evidence_required), (7, ["screenshot"]))
        with self.assertRaisesMessage(TransitionError, "Скриншоты публикации"):
            t.submit_publication(deal.pk, self.blogger, "https://instagram.com/p/1", {})
        deal = self._publish(deal)
        self.assertEqual(deal.status, S.CHECKING)
        self.assertEqual(DealEvidence.objects.filter(deal=deal, kind="screenshot").count(), 1)

    def test_payout_due_is_later_of_claim_and_retention(self):
        deal = self._publish(self._deal())
        self.assertEqual(deal.claim_until, working_days_after(deal.publication_at, 3))
        self.assertEqual(deal.retention_until, deal.publication_at + timedelta(days=7))
        self.assertEqual(deal.payout_due, max(deal.claim_until, deal.retention_until))
        short = self._publish(self._deal(min_retention_days=1))
        self.assertEqual(short.payout_due, short.claim_until)

    # ── Принять публикацию — без оплаты ──────────────────────────────────────

    def test_accept_marks_without_paying(self):
        deal = self._publish(self._deal())
        before = self._balance(self.blogger)
        t.confirm_publication(deal.pk, self.adv)
        deal.refresh_from_db()
        self.assertEqual(deal.status, S.CHECKING)
        self.assertIsNotNone(deal.publication_accepted_at)
        self.assertEqual(deal.payout_due, deal.retention_until)
        self.assertEqual(self._balance(self.blogger), before)
        self.assertTrue(Notification.objects.filter(user=self.blogger, title="Публикация принята").exists())
        with self.assertRaisesMessage(TransitionError, "уже принята"):
            t.confirm_publication(deal.pk, self.adv)
        with self.assertRaisesMessage(TransitionError, "по окончании срока сохранения"):
            t.complete(deal.pk, self.adv)

    def test_timer_pays_only_after_due(self):
        deal = self._publish(self._deal())
        self.assertEqual(t.auto_complete_overdue(), 0)
        self._age(deal, days=5)  # претензионный срок прошёл, срок сохранения (7 дн.) — нет
        self.assertEqual(t.auto_complete_overdue(), 0)
        self._age(deal, days=8)
        before = self._balance(self.blogger)
        self.assertEqual(t.auto_complete_overdue(), 1)
        deal.refresh_from_db()
        self.assertEqual(deal.status, S.COMPLETED)
        self.assertGreater(self._balance(self.blogger), before)
        note = Notification.objects.filter(user=self.blogger, title="Деньги зачислены на баланс").latest("created_at")
        self.assertIn("Сроки претензии и сохранения истекли", note.body)

    def test_accepted_then_retention_ends_pays_with_accepted_text(self):
        deal = self._publish(self._deal())
        t.confirm_publication(deal.pk, self.adv)
        self._age(deal, days=8)
        t.auto_complete_overdue()
        note = Notification.objects.filter(user=self.blogger, title="Деньги зачислены на баланс").latest("created_at")
        self.assertIn("Публикация принята, срок сохранения истёк", note.body)

    def test_dispute_possible_until_payout(self):
        deal = self._publish(self._deal())
        t.confirm_publication(deal.pk, self.adv)
        self._age(deal, days=6)  # принято, претензионный прошёл, но срок сохранения идёт
        t.open_dispute(deal.pk, self.adv, "Публикацию удалили")
        deal.refresh_from_db()
        self.assertEqual(deal.status, S.DISPUTED)
        self._age(deal, days=30)
        self.assertEqual(t.auto_complete_overdue(), 0)  # спор замораживает оплату

    # ── Сделки до правила ────────────────────────────────────────────────────

    def test_old_deal_keeps_72h_and_instant_confirm(self):
        deal = self._publish(self._deal())
        Deal.objects.filter(pk=deal.pk).update(min_retention_days=None)
        deal.refresh_from_db()
        self.assertEqual(deal.payout_due, deal.publication_at + timedelta(hours=72))
        t.confirm_publication(deal.pk, self.adv)
        deal.refresh_from_db()
        self.assertEqual(deal.status, S.COMPLETED)

    # ── Сайт и API ───────────────────────────────────────────────────────────

    def test_web_publication_with_evidence_and_accept(self):
        deal = self._deal()
        self.client.force_login(self.blogger)
        page = self.client.get(reverse("web:deal_detail", kwargs={"pk": deal.pk})).content.decode()
        self.assertIn('name="evidence_screenshot"', page)
        self.client.post(reverse("web:deal_submit_publication", kwargs={"pk": deal.pk}),
                         {"publication_url": "https://instagram.com/p/2", "evidence_screenshot": [_file(), _file("b.png")]})
        deal.refresh_from_db()
        self.assertEqual(deal.status, S.CHECKING)
        self.assertEqual(deal.evidence.count(), 2)

        self.client.force_login(self.adv)
        page = self.client.get(reverse("web:deal_detail", kwargs={"pk": deal.pk})).content.decode()
        self.assertIn("Принять публикацию", page)
        self.assertIn("data-payout-terms", page)
        self.client.post(reverse("web:deal_confirm", kwargs={"pk": deal.pk}))
        deal.refresh_from_db()
        self.assertIsNotNone(deal.publication_accepted_at)
        self.assertEqual(deal.status, S.CHECKING)
        page = self.client.get(reverse("web:deal_detail", kwargs={"pk": deal.pk})).content.decode()
        self.assertIn("data-publication-accepted", page)

    def test_api_publication_and_confirm(self):
        deal = self._deal()
        api = APIClient()
        api.force_authenticate(self.blogger)
        r = api.post(f"/api/v1/deals/{deal.pk}/submit-publication/", {"publication_url": "https://instagram.com/p/3"})
        self.assertEqual(r.status_code, 400)
        r = api.post(f"/api/v1/deals/{deal.pk}/submit-publication/",
                     {"publication_url": "https://instagram.com/p/3", "evidence_screenshot": _file()}, format="multipart")
        self.assertEqual(r.status_code, 200, r.content)
        api.force_authenticate(self.adv)
        self.assertEqual(api.post(f"/api/v1/deals/{deal.pk}/confirm-publication/").status_code, 200)
        data = api.get(f"/api/v1/deals/{deal.pk}/").json()
        self.assertIsNotNone(data["publication_accepted_at"])
        self.assertEqual(data["status"], "checking")
        self.assertEqual(len(data["evidence"]), 1)
        self.assertIsNotNone(data["payout_due"])
