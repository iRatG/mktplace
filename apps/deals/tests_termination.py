"""
#37: выход из заключённой сделки — по новым условиям одностороннего отказа нет; прекращение по соглашению сторон
(возврат рекламодателю за вычетом комиссии платформы); ранее заключённые сделки — по прежним правилам отмены.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.billing import metrics
from apps.billing.models import Wallet
from apps.campaigns.models import Campaign
from apps.campaigns.models import Response as CampaignResponse
from apps.campaigns.testing import CARD_FIELDS, deal_from_response
from apps.deals import services as t
from apps.deals.models import Deal, TerminationRequest
from apps.deals.services import TransitionError
from apps.notifications.models import Notification
from apps.platforms.models import Platform
from apps.users.models import User

S = Deal.Status
_n = 0


class TerminationTest(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.adv = User.objects.create_user(email="adv@test.com", password="pass1234", role=User.Role.ADVERTISER,
                                            status=User.Status.ACTIVE)
        Wallet.objects.filter(user=self.adv).update(available_balance=Decimal("1000000"))
        self.blogger = User.objects.create_user(email="bl@test.com", password="pass1234", role=User.Role.BLOGGER,
                                                status=User.Status.ACTIVE)
        self.campaign = Campaign.objects.create(
            advertiser=self.adv, name="Кампания", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("100000"), budget=Decimal("1000000"), status=Campaign.Status.ACTIVE,
            start_date=self.today, end_date=self.today + timedelta(days=40), approval_required=False, **CARD_FIELDS,
        )

    def _deal(self):
        global _n
        _n += 1
        platform = Platform.objects.create(
            blogger=self.blogger, social_type=Platform.SocialType.INSTAGRAM, url=f"https://instagram.com/tr{_n}",
            subscribers=12000, price_post=Decimal("50000"), status=Platform.Status.APPROVED,
        )
        resp = CampaignResponse.objects.create(campaign=self.campaign, blogger=self.blogger, platform=platform,
                                               content_type="post", proposed_price=Decimal("100000"))
        return deal_from_response(resp, self.adv, publication_date=self.today)

    def test_no_unilateral_cancel_on_new_terms(self):
        deal = self._deal()
        self.assertTrue(deal.on_package_terms)
        with self.assertRaisesMessage(TransitionError, "в одностороннем порядке нельзя"):
            t.cancel(deal.pk, self.adv)
        deal.refresh_from_db()
        self.assertEqual(deal.status, S.IN_PROGRESS)

    def test_old_deal_keeps_old_cancel(self):
        deal = self._deal()
        Deal.objects.filter(pk=deal.pk).update(min_retention_days=None)
        before = Wallet.objects.get(user=self.adv).available_balance
        t.cancel(deal.pk, self.adv)
        deal.refresh_from_db()
        self.assertEqual(deal.status, S.CANCELLED)
        self.assertEqual(Wallet.objects.get(user=self.adv).available_balance, before + deal.amount)

    def test_terminate_by_agreement_refunds_minus_commission(self):
        deal = self._deal()
        before = Wallet.objects.get(user=self.adv).available_balance
        t.propose_termination(deal.pk, self.blogger, "Заболел, не успею")
        self.assertTrue(Notification.objects.filter(user=self.adv, title="Предложено прекратить сделку").exists())
        with self.assertRaisesMessage(TransitionError, "вторая сторона"):
            t.accept_termination(deal.pk, self.blogger)
        t.accept_termination(deal.pk, self.adv)
        deal.refresh_from_db()
        self.assertEqual((deal.status, deal.paid_amount), (S.CANCELLED, Decimal("0")))
        self.assertEqual(Wallet.objects.get(user=self.adv).available_balance, before + Decimal("85000"))
        self.assertEqual(Wallet.objects.get(user=self.adv).reserved_balance, Decimal("0"))
        self.assertEqual(metrics.platform_revenue(), Decimal("15000"))
        self.assertTrue(Notification.objects.filter(user=self.blogger, title="Сделка прекращена по соглашению").exists())

    def test_decline_keeps_deal(self):
        deal = self._deal()
        t.propose_termination(deal.pk, self.adv, "Передумали")
        with self.assertRaisesMessage(TransitionError, "ещё ждёт ответа"):
            t.propose_termination(deal.pk, self.blogger, "Ещё одно")
        t.decline_termination(deal.pk, self.blogger)
        deal.refresh_from_db()
        self.assertEqual(deal.status, S.IN_PROGRESS)
        self.assertTrue(Notification.objects.filter(user=self.adv, title="Прекращение сделки отклонено").exists())
        t.propose_termination(deal.pk, self.blogger, "Теперь я")

    def test_rules(self):
        deal = self._deal()
        with self.assertRaisesMessage(TransitionError, "Укажите причину"):
            t.propose_termination(deal.pk, self.adv, "")
        t.submit_publication(deal.pk, self.blogger, "https://instagram.com/p/1")
        with self.assertRaisesMessage(TransitionError, "через претензию"):
            t.propose_termination(deal.pk, self.adv, "Не нравится")

    def test_publication_closes_pending_termination(self):
        deal = self._deal()
        t.propose_termination(deal.pk, self.adv, "Передумали")
        t.submit_publication(deal.pk, self.blogger, "https://instagram.com/p/1")
        self.assertEqual(TerminationRequest.objects.get().status, TerminationRequest.Status.CLOSED)

    def test_web_and_api(self):
        deal = self._deal()
        self.client.force_login(self.adv)
        page = self.client.get(reverse("web:deal_detail", kwargs={"pk": deal.pk})).content.decode()
        self.assertIn("data-termination-form", page)
        self.assertNotIn(reverse("web:deal_cancel", kwargs={"pk": deal.pk}), page)
        self.client.post(reverse("web:deal_propose_termination", kwargs={"pk": deal.pk}), {"reason": "Бюджет урезали"})
        api = APIClient()
        api.force_authenticate(self.blogger)
        self.assertEqual(api.post(f"/api/v1/deals/{deal.pk}/decline-termination/").status_code, 200)
        api.force_authenticate(self.adv)
        self.assertEqual(api.post(f"/api/v1/deals/{deal.pk}/cancel/").status_code, 400)
        self.assertEqual(api.post(f"/api/v1/deals/{deal.pk}/propose-termination/", {"reason": "Ещё раз"}).status_code, 200)
        api.force_authenticate(self.blogger)
        self.assertEqual(api.post(f"/api/v1/deals/{deal.pk}/accept-termination/").status_code, 200)
        deal.refresh_from_db()
        self.assertEqual(deal.status, S.CANCELLED)
