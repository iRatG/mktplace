"""
#33: сделка заключается акцептом индивидуальной оферты; резерв — при направлении оферты; срок акцепта — 3 рабочих
дня; дата публикации в оферте; даты кампании обязательны для модерации и покрывают назначенные публикации.
"""
from datetime import datetime, timedelta
from decimal import Decimal

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.billing.models import Transaction, Wallet
from apps.campaigns.models import Campaign, DirectOffer
from apps.campaigns.models import Response as CampaignResponse
from apps.campaigns.services import (
    AcceptError, accept_direct_offer, accept_response, complete_campaign, expire_overdue_responses_and_offers,
    reject_offer,
)
from apps.campaigns.validation import (
    budget_committed, deals_in_cap, publication_calendar, publication_date_error, working_days_after,
)
from apps.deals import services as transitions
from apps.deals.models import Deal
from apps.notifications.models import Notification
from apps.platforms.models import Platform
from apps.users.models import User

_n = 0


def _user(email, role=User.Role.ADVERTISER):
    return User.objects.create_user(email=email, password="pass1234", role=role, status=User.Status.ACTIVE)


def _platform(blogger):
    global _n
    _n += 1
    return Platform.objects.create(
        blogger=blogger, social_type=Platform.SocialType.INSTAGRAM, url=f"https://instagram.com/o{_n}",
        subscribers=12000, price_post=Decimal("50000"), status=Platform.Status.APPROVED,
    )


def _wallet(user):
    return Wallet.objects.get(user=user)


class WorkingDaysAfterTest(SimpleTestCase):
    def test_skips_weekend_and_keeps_time(self):
        friday = datetime(2026, 10, 9, 15, 30)
        self.assertEqual(working_days_after(friday, 3), datetime(2026, 10, 14, 15, 30))  # среда
        self.assertEqual(working_days_after(datetime(2026, 10, 10, 9, 0), 1), datetime(2026, 10, 12, 9, 0))


class OfferAcceptanceTest(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.adv = _user("adv@test.com")
        Wallet.objects.filter(user=self.adv).update(available_balance=Decimal("1000000"))
        self.blogger = _user("bl@test.com", User.Role.BLOGGER)
        self.platform = _platform(self.blogger)
        self.campaign = Campaign.objects.create(
            advertiser=self.adv, name="Кампания", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=Campaign.Status.ACTIVE,
            start_date=self.today, end_date=self.today + timedelta(days=30),
        )
        self.day = self.today + timedelta(days=5)

    def _response(self, blogger=None, price="150000"):
        blogger = blogger or self.blogger
        platform = self.platform if blogger == self.blogger else _platform(blogger)
        return CampaignResponse.objects.create(
            campaign=self.campaign, blogger=blogger, platform=platform, content_type="post",
            proposed_price=Decimal(price),
        )

    # ── Принятие отклика = оферта с резервом ─────────────────────────────────

    def test_accept_response_sends_offer_and_reserves(self):
        resp = self._response()
        offer = accept_response(resp.pk, self.adv, self.day)
        resp.refresh_from_db()
        self.assertEqual(resp.status, CampaignResponse.Status.ACCEPTED)
        self.assertEqual(offer.response, resp)
        self.assertEqual(offer.publication_date, self.day)
        self.assertEqual(offer.reserved_amount, Decimal("150000"))
        self.assertEqual(offer.terms["name"], "Кампания")
        self.assertFalse(Deal.objects.exists())
        wallet = _wallet(self.adv)
        self.assertEqual((wallet.available_balance, wallet.reserved_balance), (Decimal("850000"), Decimal("150000")))
        self.assertTrue(Transaction.objects.filter(offer=offer, type=Transaction.Type.RESERVE).exists())
        note = Notification.objects.get(user=self.blogger, title="Вам направлена оферта")
        self.assertIn(f"{self.day:%d.%m.%Y}", note.body)

    def test_offer_accept_deadline_is_three_working_days(self):
        offer = accept_response(self._response().pk, self.adv, self.day)
        self.assertEqual(offer.expires_at.date(), working_days_after(offer.created_at, 3).date())

    def test_insufficient_funds_no_offer(self):
        Wallet.objects.filter(user=self.adv).update(available_balance=Decimal("1000"))
        resp = self._response()
        with self.assertRaises(AcceptError):
            accept_response(resp.pk, self.adv, self.day)
        resp.refresh_from_db()
        self.assertEqual(resp.status, CampaignResponse.Status.PENDING)
        self.assertFalse(DirectOffer.objects.exists())
        self.assertEqual(_wallet(self.adv).reserved_balance, Decimal("0"))

    # ── Акцепт ───────────────────────────────────────────────────────────────

    def test_blogger_accepts_offer_deal_from_reserve(self):
        offer = accept_response(self._response().pk, self.adv, self.day)
        deal = accept_direct_offer(offer.pk, self.blogger)
        self.assertEqual(deal.status, Deal.Status.IN_PROGRESS)
        self.assertEqual(deal.publication_date, self.day)
        wallet = _wallet(self.adv)
        self.assertEqual((wallet.available_balance, wallet.reserved_balance), (Decimal("850000"), Decimal("150000")))
        self.assertEqual(Transaction.objects.filter(type=Transaction.Type.RESERVE).count(), 1)
        self.assertTrue(Transaction.objects.filter(offer=offer, deal=deal, type=Transaction.Type.RESERVE).exists())
        offer.refresh_from_db()
        self.assertEqual((offer.status, offer.deal), (DirectOffer.Status.ACCEPTED, deal))

    def test_legacy_offer_without_reserve_reserves_on_accept(self):
        offer = DirectOffer.objects.create(
            advertiser=self.adv, blogger=self.blogger, campaign=self.campaign, platform=self.platform,
            content_type="post", proposed_price=Decimal("150000"),
        )
        deal = accept_direct_offer(offer.pk, self.blogger)
        self.assertEqual(deal.status, Deal.Status.IN_PROGRESS)
        self.assertEqual(_wallet(self.adv).reserved_balance, Decimal("150000"))

    def test_blogger_rejects_offer_reserve_returned_can_respond_again(self):
        resp = self._response()
        offer = accept_response(resp.pk, self.adv, self.day)
        reject_offer(offer.pk, self.blogger)
        resp.refresh_from_db()
        self.assertEqual(resp.status, CampaignResponse.Status.WITHDRAWN)
        wallet = _wallet(self.adv)
        self.assertEqual((wallet.available_balance, wallet.reserved_balance), (Decimal("1000000"), Decimal("0")))
        self.assertTrue(Transaction.objects.filter(offer=offer, type=Transaction.Type.RELEASE).exists())
        self._response()  # новый отклик не упирается в ограничения
        self.assertEqual(CampaignResponse.objects.filter(status=CampaignResponse.Status.PENDING).count(), 1)

    def test_expired_offer_released_once(self):
        resp = self._response()
        offer = accept_response(resp.pk, self.adv, self.day)
        DirectOffer.objects.filter(pk=offer.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        expire_overdue_responses_and_offers()
        expire_overdue_responses_and_offers()
        offer.refresh_from_db()
        resp.refresh_from_db()
        self.assertEqual(offer.status, DirectOffer.Status.EXPIRED)
        self.assertEqual(resp.status, CampaignResponse.Status.EXPIRED)
        self.assertEqual(_wallet(self.adv).reserved_balance, Decimal("0"))
        self.assertEqual(Transaction.objects.filter(offer=offer, type=Transaction.Type.RELEASE).count(), 1)
        self.assertIn("возвращены", Notification.objects.get(user=self.adv, title="Предложение истекло без ответа").body)

    def test_campaign_finish_releases_pending_offer(self):
        offer = accept_response(self._response().pk, self.adv, self.day)
        complete_campaign(self.campaign.pk, actor=self.adv)
        offer.refresh_from_db()
        self.assertEqual(offer.status, DirectOffer.Status.EXPIRED)
        self.assertEqual(_wallet(self.adv).reserved_balance, Decimal("0"))

    # ── Бюджет и места с ожидающими офертами ─────────────────────────────────

    def test_pending_offers_take_budget_and_slots(self):
        Campaign.objects.filter(pk=self.campaign.pk).update(budget=Decimal("300000"), max_bloggers=2)
        self.campaign.refresh_from_db()
        accept_response(self._response(price="200000").pk, self.adv, self.day)
        self.assertEqual(budget_committed(self.campaign), Decimal("200000"))
        self.assertEqual(deals_in_cap(self.campaign), 1)
        with self.assertRaisesMessage(AcceptError, "Не хватает бюджета"):
            accept_response(self._response(_user("b2@test.com", User.Role.BLOGGER)).pk, self.adv, self.day)

    # ── Дата публикации ──────────────────────────────────────────────────────

    def test_publication_date_rules(self):
        self.assertIsNone(publication_date_error(self.campaign, self.day))
        self.assertIn("прошлом", publication_date_error(self.campaign, self.today - timedelta(days=1)))
        self.assertIn("окончания", publication_date_error(self.campaign, self.today + timedelta(days=31)))
        with self.assertRaisesMessage(AcceptError, "окончания"):
            accept_response(self._response().pk, self.adv, self.today + timedelta(days=31))

    def test_calendar_counts_deals_and_offers(self):
        d1, d2 = self.today + timedelta(days=3), self.today + timedelta(days=4)
        b2, b3 = _user("b2@test.com", User.Role.BLOGGER), _user("b3@test.com", User.Role.BLOGGER)
        accept_direct_offer(accept_response(self._response().pk, self.adv, d1).pk, self.blogger)
        accept_response(self._response(b2).pk, self.adv, d1)
        accept_response(self._response(b3).pk, self.adv, d2)
        self.assertEqual(publication_calendar(self.campaign), [(d1, 2), (d2, 1)])

    def test_publication_not_before_date(self):
        deal = accept_direct_offer(accept_response(self._response().pk, self.adv, self.day).pk, self.blogger)
        with self.assertRaisesMessage(transitions.TransitionError, f"не раньше {self.day:%d.%m.%Y}"):
            transitions.submit_publication(deal.pk, self.blogger, "https://instagram.com/p/1")
        self.client.force_login(self.blogger)
        page = self.client.get(reverse("web:deal_detail", kwargs={"pk": deal.pk}))
        self.assertContains(page, f"Публикация — не раньше {self.day:%d.%m.%Y}")
        self.assertNotContains(page, 'name="publication_url"')
        Deal.objects.filter(pk=deal.pk).update(publication_date=self.today)
        transitions.submit_publication(deal.pk, self.blogger, "https://instagram.com/p/1")

    # ── Сайт и API ───────────────────────────────────────────────────────────

    def test_web_accept_form_and_blogger_dashboard(self):
        resp = self._response()
        self.client.force_login(self.adv)
        page = self.client.get(reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk}))
        self.assertContains(page, 'name="publication_date"')
        self.assertContains(page, "Направить оферту")
        self.client.post(reverse("web:response_accept", kwargs={"pk": resp.pk}),
                         {"publication_date": self.day.isoformat()})
        page = self.client.get(reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk}))
        self.assertContains(page, "Оферта направлена — ждём ответа до")
        self.client.force_login(self.blogger)
        dash = self.client.get(reverse("web:blogger_dashboard"))
        self.assertContains(dash, "Принять оферту")
        self.assertContains(dash, f"публикация {self.day:%d.%m.%Y}")
        self.assertContains(self.client.get(reverse("web:my_responses")), "Вам направлена оферта")

    def test_web_accept_without_date_refused(self):
        resp = self._response()
        self.client.force_login(self.adv)
        r = self.client.post(reverse("web:response_accept", kwargs={"pk": resp.pk}))
        self.assertTrue(any("дату публикации" in str(m) for m in r.wsgi_request._messages))
        self.assertFalse(DirectOffer.objects.exists())

    def test_api_accept_requires_date(self):
        resp = self._response()
        api = APIClient()
        api.force_authenticate(self.adv)
        url = reverse("campaigns:response-accept", kwargs={"pk": resp.pk})
        self.assertEqual(api.post(url, {}, format="json").status_code, 400)
        r = api.post(url, {"publication_date": self.day.isoformat()}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["offer_id"], DirectOffer.objects.get(response=resp).pk)

    def test_direct_offer_from_catalog_with_date(self):
        self.client.force_login(self.adv)
        r = self.client.post(reverse("web:direct_offer_create", kwargs={"platform_pk": self.platform.pk}), {
            "campaign": self.campaign.pk, "content_type": "post", "publication_date": self.day.isoformat(),
        })
        self.assertRedirects(r, reverse("web:blogger_catalog"))
        offer = DirectOffer.objects.get()
        self.assertEqual((offer.publication_date, offer.reserved_amount), (self.day, Decimal("150000")))
        self.assertIsNone(offer.response)


class CampaignDatesTest(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.adv = _user("adv@test.com")
        Wallet.objects.filter(user=self.adv).update(available_balance=Decimal("1000000"))

    def _campaign(self, **extra):
        fields = dict(advertiser=self.adv, name="К", payment_type=Campaign.PaymentType.FIXED,
                      fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=Campaign.Status.DRAFT)
        fields.update(extra)
        return Campaign.objects.create(**fields)

    def test_moderation_requires_dates_web_and_api(self):
        c = self._campaign(start_date=self.today)
        self.client.force_login(self.adv)
        r = self.client.post(reverse("web:campaign_submit", kwargs={"pk": c.pk}))
        c.refresh_from_db()
        self.assertEqual(c.status, Campaign.Status.DRAFT)
        self.assertTrue(any("даты начала и окончания" in str(m) for m in r.wsgi_request._messages))
        api = APIClient()
        api.force_authenticate(self.adv)
        self.assertEqual(api.post(f"/api/v1/campaigns/{c.pk}/submit_for_moderation/").status_code, 400)

    def test_draft_saves_without_dates(self):
        c = self._campaign()
        self.assertEqual(c.status, Campaign.Status.DRAFT)

    def test_edit_cannot_cut_below_scheduled_publication(self):
        from apps.web.campaign_proposals import form_data_from_campaign

        blogger = _user("bl@test.com", User.Role.BLOGGER)
        c = self._campaign(status=Campaign.Status.ACTIVE, start_date=self.today,
                           end_date=self.today + timedelta(days=30))
        day = self.today + timedelta(days=25)
        resp = CampaignResponse.objects.create(campaign=c, blogger=blogger, platform=_platform(blogger),
                                               content_type="post")
        accept_response(resp.pk, self.adv, day)
        Campaign.objects.filter(pk=c.pk).update(status=Campaign.Status.PAUSED)
        c.refresh_from_db()
        data = form_data_from_campaign(c)
        payload = {k: data.getlist(k) for k in data}
        payload["end_date"] = (day - timedelta(days=1)).isoformat()
        self.client.force_login(self.adv)
        r = self.client.post(reverse("web:campaign_edit", kwargs={"pk": c.pk}), payload)
        self.assertIn("end_date", r.context["form"].errors)
        self.assertIn(f"{day:%d.%m.%Y}", r.context["form"].errors["end_date"][0])
        c.refresh_from_db()
        self.assertEqual(c.status, Campaign.Status.PAUSED)
