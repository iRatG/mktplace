"""Р14 (#75): Поручение рекламодателя и оферта от имени Оператора (ПЭТ 6.2, 7.3; ТО 4.3, 10.2)."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.billing.models import Wallet
from apps.campaigns.models import Campaign, DirectOffer, Mandate
from apps.campaigns.models import Response as CampaignResponse
from apps.campaigns.services import accept_direct_offer, send_offer
from apps.campaigns.testing import CARD_FIELDS, publication_day
from apps.platforms.models import Platform
from apps.users.models import User

D = Decimal


class MandateTest(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.adv = User.objects.create_user(email="adv@test.com", password="pass1234", role=User.Role.ADVERTISER,
                                            status=User.Status.ACTIVE)
        Wallet.objects.filter(user=self.adv).update(available_balance=D("1000000"))
        self.campaign = Campaign.objects.create(
            advertiser=self.adv, name="Кампания", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=D("100000"), budget=D("1000000"), status=Campaign.Status.ACTIVE,
            start_date=today, end_date=today + timedelta(days=30), **CARD_FIELDS,
        )
        self.blogger = User.objects.create_user(email="bl@test.com", password="pass1234", role=User.Role.BLOGGER,
                                                status=User.Status.ACTIVE)
        self.platform = Platform.objects.create(
            blogger=self.blogger, social_type=Platform.SocialType.INSTAGRAM, url="https://instagram.com/mandate",
            subscribers=12000, price_post=D("50000"), status=Platform.Status.APPROVED,
        )

    def _offer(self, **extra):
        return send_offer(campaign_pk=self.campaign.pk, advertiser=self.adv, blogger=self.blogger,
                          platform=self.platform, publication_date=publication_day(self.campaign), **extra)

    def test_offer_is_based_on_recorded_mandate(self):
        offer = self._offer(confirmed_ip="10.0.0.1")
        mandate = offer.mandate
        self.assertEqual(mandate.number, f"П-{mandate.pk}")
        self.assertEqual((mandate.advertiser, mandate.blogger, mandate.platform), (self.adv, self.blogger, self.platform))
        self.assertEqual((mandate.price, mandate.commission_percent, mandate.commission_amount),
                         (D("100000"), D("16"), D("16000")))
        self.assertEqual((mandate.confirmed_by, mandate.confirmed_ip, mandate.confirmed_via), (self.adv, "10.0.0.1", "web"))
        self.assertIn("06–07.11.2026", mandate.edition)
        self.assertEqual(mandate.terms["name"], "Кампания")

    def test_mandate_snapshot_survives_campaign_edit(self):
        offer = self._offer()
        Campaign.objects.filter(pk=self.campaign.pk).update(name="Другое имя", description="Новый бриф")
        offer.mandate.refresh_from_db()
        self.assertEqual(offer.mandate.terms["name"], "Кампания")

    def test_acceptance_recorded_separately_from_mandate(self):
        offer = self._offer()
        deal = accept_direct_offer(offer.pk, self.blogger)
        offer.refresh_from_db()
        self.assertLess(offer.mandate.confirmed_at, deal.created_at + timedelta(seconds=1))
        self.assertEqual(deal.direct_offer.mandate, offer.mandate)
        self.client.force_login(self.blogger)
        page = self.client.get(reverse("web:deal_detail", kwargs={"pk": deal.pk})).content.decode()
        self.assertIn("data-deal-parties", page)
        self.assertIn(offer.mandate.number, page)

    def test_blogger_sees_operator_offer_card(self):
        offer = self._offer()
        self.client.force_login(self.blogger)
        page = self.client.get(reverse("web:blogger_dashboard")).content.decode()
        self.assertIn("data-offer-operator", page)
        self.assertIn(offer.mandate.number, page)
        self.assertIn("заказчик-комиссионер", page)

    def test_web_accept_response_records_mandate_with_ip(self):
        resp = CampaignResponse.objects.create(blogger=self.blogger, campaign=self.campaign, platform=self.platform,
                                               content_type="post")
        self.client.force_login(self.adv)
        page = self.client.get(reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk})).content.decode()
        self.assertIn("data-mandate-note", page)
        self.assertIn("Подтвердить Поручение", page)
        self.client.post(reverse("web:response_accept", kwargs={"pk": resp.pk}),
                         {"publication_date": publication_day(self.campaign).isoformat()}, HTTP_X_REAL_IP="10.1.2.3")
        mandate = DirectOffer.objects.get(response=resp).mandate
        self.assertEqual((mandate.confirmed_via, mandate.confirmed_ip), ("web", "10.1.2.3"))

    def test_api_accept_response_records_mandate(self):
        resp = CampaignResponse.objects.create(blogger=self.blogger, campaign=self.campaign, platform=self.platform,
                                               content_type="post")
        api = APIClient()
        api.force_authenticate(self.adv)
        r = api.post(f"/api/v1/campaigns/responses/{resp.pk}/accept/",
                     {"publication_date": publication_day(self.campaign).isoformat()}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        mandate = Mandate.objects.get()
        self.assertEqual(r.json()["mandate"], mandate.number)
        self.assertEqual(mandate.confirmed_via, "api")
