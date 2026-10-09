"""
#34: порядок согласования материала — условие кампании и оферты. При обязательном согласовании публикация только после
одобрения, автоодобрения нет, сроки сдачи и рассмотрения (рабочие дни); без обязательного согласования — как раньше.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.billing.models import Wallet
from apps.campaigns.models import Campaign, DirectOffer
from apps.campaigns.models import Response as CampaignResponse
from apps.campaigns.services import AcceptError, accept_response
from apps.campaigns.testing import deal_from_response, publication_day
from apps.campaigns.validation import working_days_after, working_days_before
from apps.deals import services as transitions
from apps.deals.models import Deal, DealStatusLog
from apps.deals.services import TransitionError
from apps.notifications.models import Notification
from apps.platforms.models import Platform
from apps.users.models import User

S = Deal.Status
_n = 0


class ContentApprovalTest(TestCase):
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
            start_date=self.today, end_date=self.today + timedelta(days=40),
        )

    def _response(self):
        global _n
        _n += 1
        platform = Platform.objects.create(
            blogger=self.blogger, social_type=Platform.SocialType.INSTAGRAM, url=f"https://instagram.com/ca{_n}",
            subscribers=12000, price_post=Decimal("50000"), status=Platform.Status.APPROVED,
        )
        return CampaignResponse.objects.create(
            campaign=self.campaign, blogger=self.blogger, platform=platform, content_type="post",
            proposed_price=Decimal("150000"),
        )

    def _deal(self, **campaign_fields):
        if campaign_fields:
            Campaign.objects.filter(pk=self.campaign.pk).update(**campaign_fields)
            self.campaign.refresh_from_db()
        return deal_from_response(self._response(), self.adv)

    def _set(self, deal, **fields):
        Deal.objects.filter(pk=deal.pk).update(**fields)
        deal.refresh_from_db()
        return deal

    # ── Условия кампании → оферта → сделка ───────────────────────────────────

    def test_campaign_default_and_terms_go_to_deal(self):
        self.assertTrue(self.campaign.approval_required)
        deal = self._deal(content_lead_days=7, review_days=3)
        self.assertTrue(deal.approval_required)
        self.assertEqual((deal.content_lead_days, deal.review_days), (7, 3))
        # условия фиксируются при направлении оферты — правка кампании их не меняет
        Campaign.objects.filter(pk=self.campaign.pk).update(approval_required=False)
        deal.refresh_from_db()
        self.assertTrue(deal.approval_required)

    def test_old_offer_without_terms_keeps_old_rules(self):
        resp = self._response()
        offer = accept_response(resp.pk, self.adv, publication_day(self.campaign))
        terms = dict(offer.terms)
        for key in ("approval_required", "content_lead_days", "review_days"):
            terms.pop(key)
        DirectOffer.objects.filter(pk=offer.pk).update(terms=terms)
        from apps.campaigns.services import accept_direct_offer

        deal = accept_direct_offer(offer.pk, self.blogger)
        self.assertFalse(deal.approval_required)

    def test_offer_date_leaves_time_for_content(self):
        too_soon = working_days_after(self.today, 4)
        resp = self._response()
        with self.assertRaisesMessage(AcceptError, "за 5 раб. дн. до даты публикации"):
            accept_response(resp.pk, self.adv, too_soon)
        offer = accept_response(resp.pk, self.adv, working_days_after(self.today, 5))
        self.assertTrue(offer.pk)
        # без обязательного согласования — любая дата в пределах кампании
        Campaign.objects.filter(pk=self.campaign.pk).update(approval_required=False)
        self.campaign.refresh_from_db()
        self.assertTrue(accept_response(self._response().pk, self.adv, self.today).pk)

    # ── Обязательное согласование ────────────────────────────────────────────

    def test_publication_only_after_approval(self):
        deal = self._set(self._deal(), publication_date=self.today)
        with self.assertRaisesMessage(TransitionError, "только после согласования"):
            transitions.submit_publication(deal.pk, self.blogger, "https://instagram.com/p/1")
        transitions.submit_creative(deal.pk, self.blogger, "Текст")
        transitions.approve_creative(deal.pk, self.adv)
        transitions.submit_publication(deal.pk, self.blogger, "https://instagram.com/p/1")
        deal.refresh_from_db()
        self.assertEqual(deal.status, S.CHECKING)

    def test_no_auto_approval_when_required(self):
        deal = self._deal()
        transitions.submit_creative(deal.pk, self.blogger, "Текст")
        self._set(deal, creative_submitted_at=timezone.now() - timedelta(days=10))
        self.assertEqual(transitions.auto_approve_overdue_creatives(), 0)
        with self.assertRaises(TransitionError):
            transitions.approve_creative(deal.pk, actor=None)
        deal.refresh_from_db()
        self.assertEqual(deal.status, S.ON_APPROVAL)

    def test_submissions_counted_late_submission_recorded(self):
        deal = self._deal()
        transitions.submit_creative(deal.pk, self.blogger, "Первый вариант")
        transitions.reject_creative(deal.pk, self.adv, "Добавьте хештег")
        # дата публикации через 2 рабочих дня — срок сдачи (за 5 раб. дн.) уже прошёл
        deal = self._set(deal, publication_date=working_days_after(self.today, 2))
        transitions.submit_creative(deal.pk, self.blogger, "Исправленный вариант")
        deal.refresh_from_db()
        self.assertEqual(deal.creative_submissions, 2)
        self.assertEqual(deal.status, S.ON_APPROVAL)  # опоздание не мешает сдать
        log = DealStatusLog.objects.filter(deal=deal).last().comment
        self.assertIn("исправленный", log)
        self.assertIn(f"Срок сдачи по оферте — {working_days_before(deal.publication_date, 5):%d.%m.%Y}", log)

    def test_review_overdue_notifies_both_once(self):
        deal = self._deal()
        transitions.submit_creative(deal.pk, self.blogger, "Текст")
        Notification.objects.all().delete()
        self.assertEqual(transitions.notify_overdue_reviews(), 0)  # срок ещё не истёк
        deal = self._set(deal, creative_submitted_at=timezone.now() - timedelta(days=10))
        self.assertTrue(deal.review_overdue)
        self.assertEqual(transitions.notify_overdue_reviews(), 1)
        self.assertEqual(transitions.notify_overdue_reviews(), 0)
        self.assertTrue(Notification.objects.filter(user=self.adv, related_deal=deal).exists())
        self.assertTrue(Notification.objects.filter(user=self.blogger, related_deal=deal).exists())
        deal.refresh_from_db()
        self.assertEqual(deal.status, S.ON_APPROVAL)

        self.client.force_login(self.blogger)
        page = self.client.get(reverse("web:deal_detail", kwargs={"pk": deal.pk})).content.decode()
        self.assertIn("data-review-overdue", page)

        # отклонение и новая отправка — новый срок и новое уведомление при новой просрочке
        transitions.reject_creative(deal.pk, self.adv, "Правки")
        transitions.submit_creative(deal.pk, self.blogger, "Исправлено")
        deal.refresh_from_db()
        self.assertIsNone(deal.review_overdue_notified_at)

    def test_review_due_in_working_days(self):
        deal = self._deal(review_days=2)
        transitions.submit_creative(deal.pk, self.blogger, "Текст")
        deal.refresh_from_db()
        self.assertEqual(deal.review_due, working_days_after(deal.creative_submitted_at, 2))

    def test_page_blocks_publication_form_until_approved(self):
        deal = self._set(self._deal(), publication_date=self.today)
        self.client.force_login(self.blogger)
        page = self.client.get(reverse("web:deal_detail", kwargs={"pk": deal.pk})).content.decode()
        self.assertIn("data-publication-needs-approval", page)
        self.assertNotIn('name="publication_url"', page)
        self.assertIn("Обязательный шаг", page)

    # ── Без обязательного согласования — как раньше ──────────────────────────

    def test_not_required_publish_directly_and_auto_approve(self):
        deal = self._set(self._deal(approval_required=False), publication_date=self.today)
        self.assertFalse(deal.approval_required)
        other = self._deal()  # кампания уже без согласования
        transitions.submit_creative(other.pk, self.blogger, "Текст")
        self._set(other, creative_submitted_at=timezone.now() - timedelta(days=3))
        self.assertEqual(transitions.auto_approve_overdue_creatives(), 1)
        transitions.submit_publication(deal.pk, self.blogger, "https://instagram.com/p/2")
        deal.refresh_from_db()
        self.assertEqual(deal.status, S.CHECKING)

    # ── Форма и API ──────────────────────────────────────────────────────────

    def test_web_form_saves_settings_and_defaults(self):
        self.client.force_login(self.adv)
        page = self.client.get(reverse("web:campaign_create")).content.decode()
        self.assertIn('name="approval_required"', page)
        self.assertIn("checked", page.split('name="approval_required"')[1][:200])
        from apps.web.forms import CampaignForm

        base = {"name": "К", "payment_type": "fixed", "fixed_price": "150000", "budget": "1500000",
                "max_bloggers": "1", "min_subscribers": "0"}
        form = CampaignForm(data={**base, "approval_required": "on"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual((form.cleaned_data["content_lead_days"], form.cleaned_data["review_days"]), (5, 2))
        form = CampaignForm(data={**base, "content_lead_days": "0", "review_days": "11"})
        self.assertFalse(form.is_valid())
        self.assertIn("content_lead_days", form.errors)
        self.assertIn("review_days", form.errors)
        form = CampaignForm(data=base)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.cleaned_data["approval_required"])

    def test_api_fields(self):
        deal = self._deal()
        api = APIClient()
        api.force_authenticate(self.blogger)
        data = api.get(f"/api/v1/deals/{deal.pk}/").json()
        self.assertTrue(data["approval_required"])
        self.assertEqual(data["content_due"], str(working_days_before(deal.publication_date, 5)))
        api.force_authenticate(self.adv)
        data = api.get(f"/api/v1/campaigns/{self.campaign.pk}/").json()
        self.assertEqual((data["approval_required"], data["content_lead_days"], data["review_days"]), (True, 5, 2))
