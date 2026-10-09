"""
#36: претензия по сделке — любая сторона, обязательные основание / условие / требование / доказательства, сроки
объяснений и решения, пять решений сотрудника (всё исполнителю, всё рекламодателю, раздел, компенсация 30%,
дополнительные документы), раздел суммы в биллинге, бюджет по оплаченной части.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.billing import metrics
from apps.billing.models import Transaction, Wallet
from apps.campaigns.models import Campaign
from apps.campaigns.models import Response as CampaignResponse
from apps.campaigns.testing import CARD_FIELDS, CLAIM_FIELDS, deal_from_response
from apps.campaigns.validation import budget_committed, working_days_after
from apps.deals import services as t
from apps.deals.models import Claim, Deal
from apps.deals.services import TransitionError
from apps.notifications.models import Notification
from apps.platforms.models import Platform
from apps.users.models import User

S = Deal.Status
_n = 0


class ClaimsTest(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.adv = User.objects.create_user(email="adv@test.com", password="pass1234", role=User.Role.ADVERTISER,
                                            status=User.Status.ACTIVE)
        Wallet.objects.filter(user=self.adv).update(available_balance=Decimal("1000000"))
        self.blogger = User.objects.create_user(email="bl@test.com", password="pass1234", role=User.Role.BLOGGER,
                                                status=User.Status.ACTIVE)
        self.staff = User.objects.create_user(email="st@test.com", password="pass1234", is_staff=True,
                                              status=User.Status.ACTIVE)
        self.campaign = Campaign.objects.create(
            advertiser=self.adv, name="Кампания", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("100000"), budget=Decimal("1000000"), status=Campaign.Status.ACTIVE,
            start_date=self.today, end_date=self.today + timedelta(days=40), approval_required=False,
            min_retention_days=7, **CARD_FIELDS,
        )

    def _deal(self):
        global _n
        _n += 1
        platform = Platform.objects.create(
            blogger=self.blogger, social_type=Platform.SocialType.INSTAGRAM, url=f"https://instagram.com/cl{_n}",
            subscribers=12000, price_post=Decimal("50000"), status=Platform.Status.APPROVED,
        )
        resp = CampaignResponse.objects.create(campaign=self.campaign, blogger=self.blogger, platform=platform,
                                               content_type="post", proposed_price=Decimal("100000"))
        return deal_from_response(resp, self.adv, publication_date=self.today)

    def _published(self):
        deal = self._deal()
        t.submit_publication(deal.pk, self.blogger, "https://instagram.com/p/1")
        deal.refresh_from_db()
        return deal

    def _claim(self, deal, actor=None, **extra):
        return t.open_claim(deal.pk, actor or self.adv, **{**CLAIM_FIELDS, **extra})

    # ── Подача ───────────────────────────────────────────────────────────────

    def test_required_fields_and_evidence(self):
        deal = self._published()
        for field, message in (("violated_term", "условие"), ("description", "Опишите"), ("links", "доказательства")):
            with self.assertRaisesMessage(TransitionError, message):
                self._claim(deal, **{field: ""})
        with self.assertRaises(TransitionError):
            self._claim(deal, subject="nonsense")

    def test_open_claim_freezes_and_sets_deadlines(self):
        deal = self._published()
        claim = self._claim(deal)
        deal.refresh_from_db()
        self.assertEqual((deal.status, deal.is_frozen), (S.DISPUTED, True))
        second = timedelta(seconds=1)
        self.assertAlmostEqual(claim.answer_until, working_days_after(claim.created_at, 2), delta=second)
        self.assertAlmostEqual(claim.decide_until, working_days_after(claim.created_at, 5), delta=second)
        self.assertTrue(Notification.objects.filter(user=self.blogger, title="Претензия по сделке").exists())
        self.assertTrue(Notification.objects.filter(user=self.staff, title="Новая претензия").exists())
        with self.assertRaises(TransitionError):  # одна претензия за раз, сделка уже в претензии
            self._claim(deal, actor=self.blogger)

    def test_blogger_can_claim_in_progress(self):
        deal = self._deal()
        claim = self._claim(deal, actor=self.blogger, subject="no_review", demand="compensation")
        self.assertEqual(claim.respondent, self.adv)

    def test_claim_window_for_placement_and_retention(self):
        deal = self._published()
        Deal.objects.filter(pk=deal.pk).update(publication_at=timezone.now() - timedelta(days=5))
        with self.assertRaisesMessage(TransitionError, "Срок претензии по размещению истёк"):
            self._claim(deal)
        claim = self._claim(deal, subject="retention", description="Пост удалён")  # весь срок сохранения
        self.assertEqual(claim.subject, Claim.Subject.RETENTION)

    def test_after_acceptance_only_retention(self):
        deal = self._published()
        t.confirm_publication(deal.pk, self.adv)
        with self.assertRaisesMessage(TransitionError, "уже принята"):
            self._claim(deal)
        self._claim(deal, subject="retention")

    def test_no_claim_after_payout(self):
        deal = self._published()
        Deal.objects.filter(pk=deal.pk).update(publication_at=timezone.now() - timedelta(days=10))
        t.auto_complete_overdue()
        with self.assertRaisesMessage(TransitionError, "не завершена"):
            self._claim(deal, subject="retention")

    # ── Материалы ────────────────────────────────────────────────────────────

    def test_respondent_explains_author_adds(self):
        deal = self._published()
        self._claim(deal)
        t.add_claim_materials(deal.pk, self.blogger, "Пост на месте, вот скрин",
                              [SimpleUploadedFile("s.png", b"\x89PNG")])
        t.add_claim_materials(deal.pk, self.adv, "Дополнение")
        claim = Claim.objects.get(deal=deal)
        self.assertEqual(claim.explanation, "Пост на месте, вот скрин")
        self.assertIsNotNone(claim.explained_at)
        self.assertIn("Дополнение", claim.demand_details)
        self.assertEqual(claim.files.count(), 1)
        self.assertTrue(Notification.objects.filter(user=self.staff, title="Новые материалы по претензии").exists())

    # ── Решения ──────────────────────────────────────────────────────────────

    def test_decision_requires_staff_and_comment(self):
        deal = self._published()
        self._claim(deal)
        with self.assertRaises(TransitionError):
            t.resolve_claim(deal.pk, self.adv, "to_blogger", "x")
        with self.assertRaisesMessage(TransitionError, "Обоснуйте"):
            t.resolve_claim(deal.pk, self.staff, "to_blogger", "")

    def test_to_advertiser_refunds(self):
        deal = self._published()
        self._claim(deal)
        before = Wallet.objects.get(user=self.adv).available_balance
        t.resolve_claim(deal.pk, self.staff, "to_advertiser", "Пост не соответствует")
        deal.refresh_from_db()
        self.assertEqual(deal.status, S.CANCELLED)
        self.assertEqual(Wallet.objects.get(user=self.adv).available_balance, before + deal.reserved_total)
        self.assertEqual(Claim.objects.get(deal=deal).decision, Claim.Decision.TO_ADVERTISER)

    def test_split_pays_part_refunds_rest_commission_on_part(self):
        deal = self._published()
        self._claim(deal)
        adv_before = Wallet.objects.get(user=self.adv).available_balance
        bl_before = Wallet.objects.get(user=self.blogger).available_balance
        with self.assertRaisesMessage(TransitionError, "меньше суммы сделки"):
            t.resolve_claim(deal.pk, self.staff, "split", "Раздел", deal.amount)
        t.resolve_claim(deal.pk, self.staff, "split", "Частично выполнено", "40 000")
        deal.refresh_from_db()
        self.assertEqual((deal.status, deal.paid_amount), (S.COMPLETED, Decimal("40000")))
        # резерв 116 000 (100 000 + 16%): исполнителю 40 000 целиком, комиссия 16% с его части = 6 400,
        # рекламодателю возврат 116 000 − 46 400 = 69 600
        self.assertEqual(Wallet.objects.get(user=self.adv).available_balance, adv_before + Decimal("69600"))
        self.assertEqual(Wallet.objects.get(user=self.blogger).available_balance, bl_before + Decimal("40000"))
        payment = Transaction.objects.get(deal=deal, type=Transaction.Type.PAYMENT)
        self.assertEqual(payment.amount, Decimal("-46400"))
        self.assertEqual(budget_committed(self.campaign), Decimal("40000"))
        self.assertEqual(metrics.platform_revenue(), Decimal("6400"))  # комиссия только с части исполнителя
        note = Notification.objects.filter(user=self.adv, title="Решение по претензии").latest("created_at")
        self.assertIn("возвращён рекламодателю", note.body)

    def test_compensation_is_30_percent(self):
        deal = self._deal()
        self._claim(deal, actor=self.blogger, subject="no_review", demand="compensation")
        t.resolve_claim(deal.pk, self.staff, "compensation", "Рекламодатель не ответил после правок")
        deal.refresh_from_db()
        self.assertEqual(deal.paid_amount, Decimal("30000.00"))
        self.assertEqual(Claim.objects.get(deal=deal).blogger_part, Decimal("30000.00"))

    def test_extra_docs_keeps_deposit_then_back_to_decision(self):
        deal = self._published()
        claim = self._claim(deal)
        t.resolve_claim(deal.pk, self.staff, "extra_docs", "Пришлите статистику")
        claim.refresh_from_db()
        self.assertEqual(claim.status, Claim.Status.EXTRA_DOCS)
        self.assertEqual(claim.extra_docs_until.date(), working_days_after(timezone.now(), 7).date())
        deal.refresh_from_db()
        self.assertEqual(deal.status, S.DISPUTED)
        self.assertTrue(Notification.objects.filter(user=self.blogger,
                                                    title="Нужны дополнительные документы по претензии").exists())
        Claim.objects.filter(pk=claim.pk).update(extra_docs_until=timezone.now() - timedelta(hours=1))
        t.remind_claim_decisions()
        claim.refresh_from_db()
        self.assertEqual(claim.status, Claim.Status.OPEN)
        t.resolve_claim(deal.pk, self.staff, "to_blogger", "Статистика подтверждает")
        deal.refresh_from_db()
        self.assertEqual(deal.status, S.COMPLETED)

    def test_staff_reminded_day_before_decision(self):
        deal = self._published()
        claim = self._claim(deal)
        Notification.objects.filter(user=self.staff).delete()
        Claim.objects.filter(pk=claim.pk).update(decide_until=timezone.now() + timedelta(hours=2))
        self.assertEqual(t.remind_claim_decisions(), 1)
        self.assertEqual(t.remind_claim_decisions(), 0)
        self.assertTrue(Notification.objects.filter(user=self.staff, title="Срок решения по претензии").exists())

    # ── Сайт, панель, API ────────────────────────────────────────────────────

    def test_web_claim_and_panel_decision(self):
        deal = self._published()
        self.client.force_login(self.adv)
        page = self.client.get(reverse("web:deal_detail", kwargs={"pk": deal.pk})).content.decode()
        self.assertIn("data-claim-form", page)
        self.client.post(reverse("web:deal_dispute", kwargs={"pk": deal.pk}),
                         {**CLAIM_FIELDS, "links": "", "claim_files": [SimpleUploadedFile("a.png", b"\x89PNG")]})
        deal.refresh_from_db()
        self.assertEqual(deal.status, S.DISPUTED)
        page = self.client.get(reverse("web:deal_detail", kwargs={"pk": deal.pk})).content.decode()
        self.assertIn("data-claim-deadlines", page)

        self.client.force_login(self.blogger)
        self.client.post(reverse("web:deal_claim_materials", kwargs={"pk": deal.pk}), {"text": "Объяснение"})
        self.assertEqual(Claim.objects.get(deal=deal).explanation, "Объяснение")

        self.client.force_login(self.staff)
        panel = self.client.get(reverse("web:admin_disputes")).content.decode()
        self.assertIn("data-claim-details", panel)
        self.assertIn("Объяснение", panel)
        self.client.post(reverse("web:admin_dispute_resolve", kwargs={"pk": deal.pk}),
                         {"decision": "split", "blogger_part": "50000", "comment": "Половина"})
        deal.refresh_from_db()
        self.assertEqual(deal.paid_amount, Decimal("50000"))

    def test_api_claim(self):
        deal = self._published()
        api = APIClient()
        api.force_authenticate(self.adv)
        r = api.post(f"/api/v1/deals/{deal.pk}/claim/", {**CLAIM_FIELDS, "links": ""}, format="json")
        self.assertEqual(r.status_code, 400)
        r = api.post(f"/api/v1/deals/{deal.pk}/claim/", CLAIM_FIELDS, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        api.force_authenticate(self.blogger)
        r = api.post(f"/api/v1/deals/{deal.pk}/claim-materials/", {"text": "Ответ"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
