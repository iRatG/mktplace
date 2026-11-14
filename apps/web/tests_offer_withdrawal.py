"""Р17 (#70): рекламодатель отзывает оферту до Акцепта (ПЭТ 6.4, 11.1, редакция 07.11.2026)."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.billing.models import Transaction, Wallet
from apps.billing.services import BillingService
from apps.campaigns.models import Campaign, DirectOffer
from apps.campaigns.models import Response as CampaignResponse
from apps.campaigns.services import AcceptError, accept_direct_offer, accept_response, send_offer, withdraw_offer
from apps.campaigns.testing import CARD_FIELDS, publication_day
from apps.campaigns.validation import deals_in_cap
from apps.deals.models import Deal
from apps.notifications.models import Notification
from apps.platforms.models import Platform
from apps.users.models import User

D = Decimal


class OfferWithdrawalTest(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.adv = User.objects.create_user(email="adv@test.com", password="pass1234", role=User.Role.ADVERTISER,
                                            status=User.Status.ACTIVE)
        Wallet.objects.filter(user=self.adv).update(available_balance=D("1000000"))
        self.campaign = Campaign.objects.create(
            advertiser=self.adv, name="Кампания", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=D("100000"), budget=D("1000000"), status=Campaign.Status.ACTIVE, max_bloggers=1,
            start_date=today, end_date=today + timedelta(days=30), **CARD_FIELDS,
        )
        self.blogger = User.objects.create_user(email="bl@test.com", password="pass1234", role=User.Role.BLOGGER,
                                                status=User.Status.ACTIVE)
        self.platform = Platform.objects.create(
            blogger=self.blogger, social_type=Platform.SocialType.INSTAGRAM, url="https://instagram.com/wd",
            subscribers=12000, price_post=D("50000"), status=Platform.Status.APPROVED,
        )

    def _offer(self):
        return send_offer(campaign_pk=self.campaign.pk, advertiser=self.adv, blogger=self.blogger,
                          platform=self.platform, publication_date=publication_day(self.campaign))

    def test_withdraw_frees_place_notifies_and_blocks_accept(self):
        offer = self._offer()
        self.assertEqual(deals_in_cap(self.campaign), 1)
        withdraw_offer(offer.pk, self.adv, "Изменился бриф")
        offer.refresh_from_db()
        self.assertEqual(offer.status, DirectOffer.Status.WITHDRAWN)
        self.assertIsNotNone(offer.withdrawn_at)
        self.assertEqual(offer.withdrawal_reason, "Изменился бриф")
        self.assertEqual(deals_in_cap(self.campaign), 0)
        note = Notification.objects.get(user=self.blogger, type=Notification.Type.DIRECT_OFFER_WITHDRAWN)
        self.assertIn("Изменился бриф", note.body)
        with self.assertRaisesMessage(AcceptError, "отозвал"):
            accept_direct_offer(offer.pk, self.blogger)
        self.assertFalse(Deal.objects.exists())
        # Место освободилось — можно направить новую оферту той же площадке.
        self.assertEqual(self._offer().status, DirectOffer.Status.PENDING)

    def test_only_owner_and_only_pending(self):
        offer = self._offer()
        stranger = User.objects.create_user(email="x@test.com", password="pass1234", role=User.Role.ADVERTISER)
        with self.assertRaisesMessage(AcceptError, "не найдена"):
            withdraw_offer(offer.pk, stranger)
        with self.assertRaisesMessage(AcceptError, "не найдена"):
            withdraw_offer(offer.pk, self.blogger)
        accept_direct_offer(offer.pk, self.blogger)
        with self.assertRaisesMessage(AcceptError, "только оферту, которая ждёт ответа"):
            withdraw_offer(offer.pk, self.adv)

    def test_offer_by_response_marks_response_rejected(self):
        resp = CampaignResponse.objects.create(blogger=self.blogger, campaign=self.campaign, platform=self.platform,
                                               content_type="post")
        offer = accept_response(resp.pk, self.adv, publication_day(self.campaign))
        withdraw_offer(offer.pk, self.adv)
        resp.refresh_from_db()
        self.assertEqual(resp.status, CampaignResponse.Status.REJECTED)
        self.assertIn("отозвал оферту", resp.rejection_reason)

    def test_legacy_reserved_offer_returns_reserve(self):
        offer = self._offer()
        DirectOffer.objects.filter(pk=offer.pk).update(reserve_on_accept=False, reserved_at=timezone.now())
        offer.refresh_from_db()
        BillingService.reserve_for_offer(offer)
        withdraw_offer(offer.pk, self.adv)
        wallet = Wallet.objects.get(user=self.adv)
        self.assertEqual((wallet.available_balance, wallet.reserved_balance), (D("1000000"), D("0")))
        self.assertTrue(Transaction.objects.filter(offer=offer, type=Transaction.Type.RELEASE).exists())

    def test_web_withdraw_from_campaign_page(self):
        offer = self._offer()
        self.client.force_login(self.adv)
        page = self.client.get(reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk})).content.decode()
        self.assertIn("data-pending-direct-offers", page)
        self.assertIn(reverse("web:direct_offer_withdraw", kwargs={"pk": offer.pk}), page)
        response = self.client.post(reverse("web:direct_offer_withdraw", kwargs={"pk": offer.pk}), {"reason": ""})
        self.assertRedirects(response, reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk}))
        offer.refresh_from_db()
        self.assertEqual(offer.status, DirectOffer.Status.WITHDRAWN)
        # Блогер не может отозвать чужую оферту через сайт.
        self.client.force_login(self.blogger)
        self.assertEqual(self.client.post(reverse("web:direct_offer_withdraw", kwargs={"pk": offer.pk})).status_code, 404)

    def test_api_withdraw_offer_by_response(self):
        resp = CampaignResponse.objects.create(blogger=self.blogger, campaign=self.campaign, platform=self.platform,
                                               content_type="post")
        offer = accept_response(resp.pk, self.adv, publication_day(self.campaign))
        api = APIClient()
        api.force_authenticate(self.adv)
        response = api.post(f"/api/v1/campaigns/responses/{resp.pk}/withdraw-offer/", {"reason": "Бюджет"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        offer.refresh_from_db()
        self.assertEqual((offer.status, offer.withdrawal_reason), (DirectOffer.Status.WITHDRAWN, "Бюджет"))
        self.assertTrue(Notification.objects.filter(user=self.blogger, type=Notification.Type.DIRECT_OFFER_WITHDRAWN).exists())
        again = api.post(f"/api/v1/campaigns/responses/{resp.pk}/withdraw-offer/", {}, format="json")
        self.assertEqual(again.status_code, 400)
