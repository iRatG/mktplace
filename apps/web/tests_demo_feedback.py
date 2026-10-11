"""
Замечания демо-прогона (#49, #50): форма кампании (шаг бюджета, календарь без прошедших дней, недостающее для модерации)
и дашборды (оферта первой, перенос даты публикации и принятая публикация видны без уведомлений).
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import Wallet
from apps.campaigns.models import Campaign
from apps.campaigns.models import Response as CampaignResponse
from apps.campaigns.services import accept_response
from apps.campaigns.testing import CARD_FIELDS, deal_from_response
from apps.campaigns.validation import moderation_missing
from apps.deals import services as transitions
from apps.deals.models import Deal
from apps.notifications.models import Notification
from apps.platforms.models import Platform
from apps.users.models import User


class _Base(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.adv = User.objects.create_user(email="adv@test.com", password="pass1234", role=User.Role.ADVERTISER,
                                            status=User.Status.ACTIVE)
        Wallet.objects.filter(user=self.adv).update(available_balance=Decimal("5000000"))
        self.blogger = User.objects.create_user(email="bl@test.com", password="pass1234", role=User.Role.BLOGGER,
                                                status=User.Status.ACTIVE)
        self.platform = Platform.objects.create(
            blogger=self.blogger, social_type=Platform.SocialType.INSTAGRAM, url="https://instagram.com/demo",
            subscribers=12000, price_post=Decimal("50000"), status=Platform.Status.APPROVED,
        )
        self.campaign = Campaign.objects.create(
            advertiser=self.adv, name="Демо", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=Campaign.Status.ACTIVE,
            start_date=self.today, end_date=self.today + timedelta(days=30), approval_required=False,
        )

    def _response(self):
        return CampaignResponse.objects.create(
            campaign=self.campaign, blogger=self.blogger, platform=self.platform, content_type="post",
            proposed_price=Decimal("150000"),
        )

    def _deal(self):
        return deal_from_response(self._response(), self.adv, publication_date=self.today + timedelta(days=2))


class CampaignFormTest(_Base):
    def test_budget_step_and_date_min(self):
        self.client.force_login(self.adv)
        html = self.client.get(reverse("web:campaign_create")).content.decode()
        self.assertIn('data-step="100000"', html)
        today = self.today.isoformat()
        for name in ("start_date", "end_date", "content_start", "deadline"):
            self.assertRegex(html, rf'name="{name}" id="id_{name}" min="{today}"')
        self.assertGreaterEqual(html.count("data-for-moderation"), 4)

    def test_increase_budget_step(self):
        self.client.force_login(self.adv)
        html = self.client.get(reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk})).content.decode()
        self.assertIn('data-step="100000"', html)

    def test_moderation_missing_lists_everything(self):
        draft = Campaign.objects.create(
            advertiser=self.adv, name="Черновик", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("1500000"), subject="Крем",
        )
        missing = moderation_missing(draft)
        self.assertEqual(missing[0], "даты начала и окончания кампании")
        self.assertIn("форматы контента", missing)
        self.assertIn("площадки", missing)
        self.assertNotIn("что рекламируем", missing)

        self.client.force_login(self.adv)
        html = self.client.get(reverse("web:campaign_detail", kwargs={"pk": draft.pk})).content.decode()
        self.assertIn("data-moderation-missing", html)
        self.assertIn("форматы контента, площадки", html)
        self.assertIn(reverse("web:campaign_edit", kwargs={"pk": draft.pk}), html)

    def test_complete_draft_has_no_missing_block(self):
        User.objects.filter(pk=self.adv.pk).update(is_demo=True)  # подтверждённый аккаунт — кнопка отправки видна
        draft = Campaign.objects.create(
            advertiser=self.adv, name="Полный", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("1500000"),
            start_date=self.today, end_date=self.today + timedelta(days=30), **CARD_FIELDS,
        )
        self.assertEqual(moderation_missing(draft), [])
        self.client.force_login(self.adv)
        html = self.client.get(reverse("web:campaign_detail", kwargs={"pk": draft.pk})).content.decode()
        self.assertNotIn("data-moderation-missing", html)
        self.assertIn(reverse("web:campaign_submit", kwargs={"pk": draft.pk}), html)

    def test_form_stays_novalidate(self):
        """min ограничивает только календарь: форма novalidate, сохранённую старую дату проверяет сервер."""
        self.client.force_login(self.adv)
        html = self.client.get(reverse("web:campaign_create")).content.decode()
        self.assertIn("novalidate", html)


class DashboardTest(_Base):
    def test_offer_is_first_in_blogger_action_block(self):
        self._deal()
        accept_response(self._second_response().pk, self.adv, self.today + timedelta(days=3))
        self.client.force_login(self.blogger)
        html = self.client.get(reverse("web:blogger_dashboard")).content.decode()
        block = html[html.index("Требует действия"):]
        self.assertIn("data-action-offer", block)
        self.assertLess(block.index("data-action-offer"), block.index("Сделка #"))
        self.assertIn(reverse("web:direct_offer_accept", kwargs={"pk": self.campaign.direct_offers.filter(
            status="pending").get().pk}), block)

    def _second_response(self):
        other = Platform.objects.create(
            blogger=self.blogger, social_type=Platform.SocialType.TELEGRAM, url="https://t.me/demo",
            subscribers=5000, price_post=Decimal("50000"), status=Platform.Status.APPROVED,
        )
        second = Campaign.objects.create(
            advertiser=self.adv, name="Вторая", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=Campaign.Status.ACTIVE,
            start_date=self.today, end_date=self.today + timedelta(days=30), approval_required=False,
        )
        self.campaign = second
        return CampaignResponse.objects.create(
            campaign=second, blogger=self.blogger, platform=other, content_type="post",
            proposed_price=Decimal("150000"),
        )

    def test_date_change_waits_for_other_side(self):
        deal = self._deal()
        new_date = self.today + timedelta(days=5)
        transitions.propose_publication_date(deal.pk, self.blogger, new_date)

        self.client.force_login(self.adv)
        html = self.client.get(reverse("web:advertiser_dashboard")).content.decode()
        self.assertIn("data-action-date-change", html)
        self.assertIn(f"на {new_date:%d.%m.%Y} — ответьте", html)
        self.assertIn(reverse("web:deal_detail", kwargs={"pk": deal.pk}) + "#date-change", html)

        self.client.force_login(self.blogger)
        html = self.client.get(reverse("web:blogger_dashboard")).content.decode()
        self.assertNotIn("data-action-date-change", html)
        self.assertIn(f"Перенос на {new_date:%d.%m.%Y} — ждём ответа", html)
        html = self.client.get(reverse("web:deal_list")).content.decode()
        self.assertIn(f"Перенос на {new_date:%d.%m.%Y} — ждём ответа", html)

        note = Notification.objects.filter(user=self.adv, related_deal=deal).latest("created_at")
        self.assertTrue(note.target_url.endswith("#date-change"))

    def test_strangers_do_not_see_date_change(self):
        deal = self._deal()
        transitions.propose_publication_date(deal.pk, self.blogger, self.today + timedelta(days=5))
        stranger = User.objects.create_user(email="x@test.com", password="pass1234", role=User.Role.ADVERTISER,
                                            status=User.Status.ACTIVE)
        self.client.force_login(stranger)
        html = self.client.get(reverse("web:advertiser_dashboard")).content.decode()
        self.assertNotIn("data-action-date-change", html)

    def test_publication_accepted_in_blogger_row(self):
        deal = self._deal()
        Deal.objects.filter(pk=deal.pk).update(publication_date=self.today)
        transitions.submit_publication(deal.pk, self.blogger, "https://instagram.com/p/demo")
        transitions.confirm_publication(deal.pk, self.adv)
        deal.refresh_from_db()
        expected = f"Публикация принята — оплата {timezone.localtime(deal.payout_due):%d.%m.%Y}"
        self.client.force_login(self.blogger)
        self.assertIn(expected, self.client.get(reverse("web:blogger_dashboard")).content.decode())
        self.assertIn(expected, self.client.get(reverse("web:deal_list")).content.decode())

    def test_dashboard_queries_do_not_grow_with_deals(self):
        self._deal()
        self.client.force_login(self.blogger)
        self.client.get(reverse("web:blogger_dashboard"))
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as one:
            self.client.get(reverse("web:blogger_dashboard"))
        self._deal_on(self._second_response())
        self.client.get(reverse("web:blogger_dashboard"))  # уведомления о новой сделке гасятся первым заходом
        with CaptureQueriesContext(connection) as two:
            self.client.get(reverse("web:blogger_dashboard"))
        a = [q["sql"][:150] for q in one.captured_queries]
        b = [q["sql"][:150] for q in two.captured_queries]
        self.assertEqual(len(one), len(two), " | ".join(x for x in b if b.count(x) > a.count(x)))

    def _deal_on(self, response):
        return deal_from_response(response, self.adv, publication_date=self.today + timedelta(days=2))
