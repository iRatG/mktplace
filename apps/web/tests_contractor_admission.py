"""Р15 (#68): откликаться, получать оферты и заключать сделки может только исполнитель с подтверждённым статусом
ИП, самозанятого или юрлица (ПС 5.2, 5.3, редакция 07.11.2026). Физлицо видит кампании, но не откликается."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.billing.models import Wallet
from apps.billing.payout_requisites import PayoutRequisitesError, submit_payout_requisites
from apps.campaigns.models import Campaign, CampaignInvitation, DirectOffer
from apps.campaigns.models import Response as CampaignResponse
from apps.campaigns.services import AcceptError, accept_direct_offer, invite_bloggers, send_offer
from apps.campaigns.testing import CARD_FIELDS, deal_from_response, publication_day
from apps.deals.models import Deal
from apps.platforms.models import Platform
from apps.profiles.admission import NOT_ADMITTED_MESSAGE, is_admitted
from apps.profiles.models import BloggerProfile
from apps.profiles.testing import make_individual
from apps.registration.models import IdentityVerification
from apps.users.models import User

_n = 0


def _blogger(email):
    global _n
    _n += 1
    user = User.objects.create_user(email=email, password="pass1234", role=User.Role.BLOGGER, status=User.Status.ACTIVE)
    Platform.objects.create(blogger=user, social_type=Platform.SocialType.INSTAGRAM, url=f"https://instagram.com/adm{_n}",
                            subscribers=12000, price_post=Decimal("50000"), status=Platform.Status.APPROVED)
    BloggerProfile.objects.filter(user=user).update(show_in_catalog=True)
    return user


class AdmissionRuleTest(TestCase):
    def test_only_confirmed_category_is_admitted(self):
        user = _blogger("a@test.com")
        self.assertTrue(is_admitted(user))  # тестовые данные по умолчанию — подтверждённый самозанятый
        make_individual(user)
        self.assertFalse(is_admitted(user))
        # Категория выбрана, но не подтверждена — не допущен.
        BloggerProfile.objects.filter(user=user).update(category=BloggerProfile.Category.IP)
        self.assertFalse(is_admitted(user))
        BloggerProfile.objects.filter(user=user).update(is_ip_confirmed=True)
        self.assertTrue(is_admitted(user))
        BloggerProfile.objects.filter(user=user).update(
            category=BloggerProfile.Category.LEGAL_ENTITY, is_ip_confirmed=False, is_legal_entity_confirmed=True)
        self.assertTrue(is_admitted(user))

    def test_new_user_on_site_is_individual(self):
        """На сайте новый блогер — физлицо: значение по умолчанию модели не изменилось."""
        self.assertEqual(BloggerProfile._meta.get_field("category").default, BloggerProfile.Category.INDIVIDUAL)


class AdmissionEntryPointsTest(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.adv = User.objects.create_user(email="adv@test.com", password="pass1234", role=User.Role.ADVERTISER,
                                            status=User.Status.ACTIVE)
        Wallet.objects.filter(user=self.adv).update(available_balance=Decimal("1000000"))
        self.campaign = Campaign.objects.create(
            advertiser=self.adv, name="Кампания", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("100000"), budget=Decimal("1000000"), status=Campaign.Status.ACTIVE,
            start_date=today, end_date=today + timedelta(days=30), **CARD_FIELDS,
        )
        self.person = make_individual(_blogger("person@test.com"))
        self.pro = _blogger("pro@test.com")

    def _platform(self, user):
        return Platform.objects.get(blogger=user)

    def test_web_respond_refused_for_individual(self):
        self.client.force_login(self.person)
        url = reverse("web:campaign_respond", kwargs={"pk": self.campaign.pk})
        response = self.client.post(url, {"platform": self._platform(self.person).pk, "content_type": "post"}, follow=True)
        self.assertContains(response, "подтверждённым статусом")
        self.assertFalse(CampaignResponse.objects.filter(blogger=self.person).exists())

    def test_web_respond_works_for_confirmed(self):
        self.client.force_login(self.pro)
        url = reverse("web:campaign_respond", kwargs={"pk": self.campaign.pk})
        self.client.post(url, {"platform": self._platform(self.pro).pk, "content_type": "post"})
        self.assertTrue(CampaignResponse.objects.filter(blogger=self.pro).exists())

    def test_api_respond_refused_for_individual(self):
        api = APIClient()
        api.force_authenticate(self.person)
        response = api.post("/api/v1/campaigns/responses/", {
            "campaign": self.campaign.pk, "platform": self._platform(self.person).pk, "content_type": "post",
        }, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn(NOT_ADMITTED_MESSAGE, response.content.decode())
        self.assertFalse(CampaignResponse.objects.filter(blogger=self.person).exists())

    def test_campaign_page_shows_status_banner_instead_of_form(self):
        self.client.force_login(self.person)
        page = self.client.get(reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk})).content.decode()
        self.assertIn("data-status-required", page)
        self.assertNotIn(reverse("web:campaign_respond", kwargs={"pk": self.campaign.pk}), page)

        self.client.force_login(self.pro)
        page = self.client.get(reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk})).content.decode()
        self.assertNotIn("data-status-required", page)

    def test_dashboard_asks_to_confirm_status(self):
        self.client.force_login(self.person)
        self.assertContains(self.client.get(reverse("web:blogger_dashboard")), "data-status-required")
        self.client.force_login(self.pro)
        self.assertNotContains(self.client.get(reverse("web:blogger_dashboard")), "data-status-required")

    def test_direct_offer_refused_for_individual(self):
        with self.assertRaisesMessage(AcceptError, "не подтвердил статус"):
            send_offer(campaign_pk=self.campaign.pk, advertiser=self.adv, blogger=self.person,
                       platform=self._platform(self.person), publication_date=publication_day(self.campaign))
        self.assertFalse(DirectOffer.objects.filter(blogger=self.person).exists())

    def test_offer_by_response_refused_when_status_lost(self):
        """Отклик подан со статусом, статус потом снят — оферту по отклику направить нельзя."""
        resp = CampaignResponse.objects.create(blogger=self.pro, campaign=self.campaign, platform=self._platform(self.pro),
                                               content_type="post")
        make_individual(self.pro)
        with self.assertRaises(AcceptError):
            deal_from_response(resp, self.adv)
        self.assertFalse(DirectOffer.objects.filter(blogger=self.pro).exists())

    def test_accept_refused_for_individual(self):
        """Оферта ушла исполнителю со статусом, к Акцепту статуса нет — сделка не заключается."""
        offer = send_offer(campaign_pk=self.campaign.pk, advertiser=self.adv, blogger=self.pro,
                           platform=self._platform(self.pro), publication_date=publication_day(self.campaign))
        make_individual(self.pro)
        with self.assertRaisesMessage(AcceptError, NOT_ADMITTED_MESSAGE):
            accept_direct_offer(offer.pk, self.pro)
        self.assertFalse(Deal.objects.filter(blogger=self.pro).exists())

    def test_catalog_lists_only_admitted(self):
        self.client.force_login(self.adv)
        platforms = list(self.client.get(reverse("web:blogger_catalog")).context["platforms"])
        owners = {p.blogger_id for p in platforms}
        self.assertIn(self.pro.pk, owners)
        self.assertNotIn(self.person.pk, owners)

    def test_invitations_skip_individuals(self):
        self.campaign.visibility = Campaign.Visibility.CLOSED
        self.campaign.save(update_fields=["visibility"])
        self.assertEqual(invite_bloggers(self.campaign.pk, self.adv, [self.person, self.pro]), 1)
        self.assertEqual(list(CampaignInvitation.objects.values_list("blogger_id", flat=True)), [self.pro.pk])


class AdmissionRequisitesTest(TestCase):
    def setUp(self):
        self.person = make_individual(_blogger("person@test.com"))
        IdentityVerification.objects.create(user=self.person, full_name="Иван Иванов", phone="+998901112233",
                                            pinfl="12345678901234", status="verified", verified_at=timezone.now())

    def test_individual_without_deals_cannot_submit_requisites(self):
        with self.assertRaisesMessage(PayoutRequisitesError, "после подтверждения статуса"):
            submit_payout_requisites(self.person, card_number="8600123456789012", card_holder_name="Иван Иванов")

    def test_individual_with_old_deal_still_submits_requisites(self):
        """Сделка физлица, заключённая до правила, доходит до выплаты — реквизиты принимаются."""
        today = timezone.localdate()
        adv = User.objects.create_user(email="adv2@test.com", password="pass1234", role=User.Role.ADVERTISER,
                                       status=User.Status.ACTIVE)
        campaign = Campaign.objects.create(
            advertiser=adv, name="Старая", payment_type=Campaign.PaymentType.FIXED, fixed_price=Decimal("100000"),
            budget=Decimal("1000000"), status=Campaign.Status.ACTIVE, start_date=today,
            end_date=today + timedelta(days=30), **CARD_FIELDS,
        )
        Deal.objects.create(campaign=campaign, blogger=self.person, advertiser=adv,
                            platform=Platform.objects.get(blogger=self.person), amount=Decimal("100000"))
        application = submit_payout_requisites(self.person, card_number="8600123456789012", card_holder_name="Иван Иванов")
        self.assertIsNotNone(application.pk)
