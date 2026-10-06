"""
Решение бизнеса 06.10.2026 (опросник по camp_test_2, вопрос 1 — Б): бюджет кампании не замораживается, но сделок по
кампании нельзя заключить больше, чем на её бюджет. Одно правило для отклика (сайт и API) и прямого предложения.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.billing.models import Wallet
from apps.campaigns.models import Campaign, DirectOffer
from apps.campaigns.models import Response as CampaignResponse
from apps.campaigns.validation import budget_committed, budget_remaining
from apps.deals.models import Deal
from apps.platforms.models import Platform
from apps.users.models import User
from apps.web.campaign_proposals import form_data_from_campaign

_n = 0


def _user(email, role):
    return User.objects.create_user(email=email, password="pass1234", role=role, status=User.Status.ACTIVE)


def _platform(blogger):
    global _n
    _n += 1
    return Platform.objects.create(
        blogger=blogger, social_type=Platform.SocialType.INSTAGRAM, url=f"https://instagram.com/b{_n}",
        subscribers=12000, price_post=Decimal("50000"), status=Platform.Status.APPROVED,
    )


class CampaignBudgetTest(TestCase):
    def setUp(self):
        self.adv = _user("adv@test.com", User.Role.ADVERTISER)
        Wallet.objects.filter(user=self.adv).update(available_balance=Decimal("10000000"))
        self.campaign = Campaign.objects.create(
            advertiser=self.adv, name="К", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("300000"), status=Campaign.Status.ACTIVE,
        )
        self.b1 = _user("b1@test.com", User.Role.BLOGGER)
        self.b2 = _user("b2@test.com", User.Role.BLOGGER)

    def _response(self, blogger, price):
        return CampaignResponse.objects.create(
            campaign=self.campaign, blogger=blogger, platform=_platform(blogger), proposed_price=Decimal(price),
        )

    def _accept_web(self, resp):
        self.client.force_login(self.adv)
        return self.client.post(reverse("web:response_accept", kwargs={"pk": resp.pk}))

    def test_committed_and_remaining(self):
        self._accept_web(self._response(self.b1, "200000"))
        self.assertEqual(budget_committed(self.campaign), Decimal("200000"))
        self.assertEqual(budget_remaining(self.campaign), Decimal("100000"))

    def test_web_accept_over_budget_refused(self):
        self._accept_web(self._response(self.b1, "200000"))
        second = self._response(self.b2, "150000")
        r = self._accept_web(second)
        second.refresh_from_db()
        self.assertEqual(second.status, CampaignResponse.Status.PENDING)
        self.assertEqual(Deal.objects.count(), 1)
        msgs = [str(m) for m in r.wsgi_request._messages]
        self.assertTrue(any("Не хватает бюджета" in m for m in msgs), msgs)

    def test_web_accept_exactly_remaining_ok(self):
        self._accept_web(self._response(self.b1, "200000"))
        self._accept_web(self._response(self.b2, "100000"))
        self.assertEqual(Deal.objects.count(), 2)

    def test_cancelled_deal_frees_budget(self):
        self._accept_web(self._response(self.b1, "200000"))
        Deal.objects.update(status=Deal.Status.CANCELLED)
        self._accept_web(self._response(self.b2, "300000"))
        self.assertEqual(Deal.objects.exclude(status=Deal.Status.CANCELLED).count(), 1)

    def test_api_accept_over_budget_refused(self):
        resp = self._response(self.b1, "400000")
        api = APIClient()
        api.force_authenticate(self.adv)
        r = api.post(reverse("campaigns:response-accept", kwargs={"pk": resp.pk}))
        self.assertEqual(r.status_code, 400)
        self.assertIn("бюджета", r.json()["detail"])
        self.assertFalse(Deal.objects.exists())

    def test_direct_offer_over_budget_refused(self):
        offer = DirectOffer.objects.create(
            advertiser=self.adv, blogger=self.b1, campaign=self.campaign,
            platform=_platform(self.b1), proposed_price=Decimal("400000"),
        )
        self.client.force_login(self.b1)
        self.client.post(reverse("web:direct_offer_accept", kwargs={"pk": offer.pk}))
        offer.refresh_from_db()
        self.assertEqual(offer.status, DirectOffer.Status.PENDING)
        self.assertFalse(Deal.objects.exists())

    def test_budget_cannot_be_lowered_below_committed(self):
        self._accept_web(self._response(self.b1, "200000"))
        Campaign.objects.filter(pk=self.campaign.pk).update(status=Campaign.Status.PAUSED)
        self.campaign.refresh_from_db()
        data = form_data_from_campaign(self.campaign)
        payload = {k: data.getlist(k) for k in data}
        payload["budget"] = "150000"
        payload["fixed_price"] = "100000"
        self.client.force_login(self.adv)
        r = self.client.post(reverse("web:campaign_edit", kwargs={"pk": self.campaign.pk}), payload)
        self.assertIn("budget", r.context["form"].errors)

    def test_owner_sees_budget_and_warnings(self):
        self._accept_web(self._response(self.b1, "200000"))
        self._response(self.b2, "160000")
        page = self.client.get(reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk}))
        self.assertContains(page, "занято сделками 200 000")
        self.assertContains(page, "не хватает остатка бюджета")
        self.assertContains(page, "выше цены кампании")
