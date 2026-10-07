"""
Дашборды и навигация (GitHub issue #3 + п. 3 из #7, openspec change improve-dashboard-navigation):
плашки-ссылки, «Требует действия», фильтр списка сделок, «Мои отклики», данные блогера в откликах,
цена отклика с разрядами, адреса двух уведомлений.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.campaigns.models import Campaign
from apps.campaigns.models import Response as CampaignResponse
from apps.deals.models import Deal
from apps.notifications.models import Notification
from apps.notifications.service import NotificationService
from apps.platforms.models import Platform
from apps.profiles.models import BloggerProfile

User = get_user_model()
_n = 0


def _user(role, **extra):
    global _n
    _n += 1
    user = User.objects.create_user(email=f"nav{_n}@test.com", password="pass1234", role=role)
    user.status = User.Status.ACTIVE
    user.is_email_confirmed = True
    user.save(update_fields=["status", "is_email_confirmed"])
    return user


def _platform(blogger):
    global _n
    _n += 1
    return Platform.objects.create(
        blogger=blogger, social_type=Platform.SocialType.INSTAGRAM,
        url=f"https://instagram.com/nav{_n}", subscribers=125000, avg_views=4000,
        engagement_rate=Decimal("3.50"), status=Platform.Status.APPROVED,
    )


def _campaign(advertiser, status=Campaign.Status.ACTIVE, name="Кампания"):
    return Campaign.objects.create(
        advertiser=advertiser, name=name, payment_type=Campaign.PaymentType.FIXED,
        fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=status,
    )


def _deal(advertiser, blogger, status, **extra):
    return Deal.objects.create(
        campaign=_campaign(advertiser), blogger=blogger, platform=_platform(blogger),
        advertiser=advertiser, amount=Decimal("150000"), status=status, **extra,
    )


class BloggerDashboardTest(TestCase):
    def setUp(self):
        self.adv = _user(User.Role.ADVERTISER)
        self.blogger = _user(User.Role.BLOGGER)
        self.client.force_login(self.blogger)

    def test_cards_are_links(self):
        html = self.client.get(reverse("web:blogger_dashboard")).content.decode()
        for href in (reverse("web:wallet"), reverse("web:my_responses"),
                     reverse("web:deal_list") + "?status=active", reverse("web:deal_list") + "?status=completed"):
            self.assertIn(f'href="{href}"', html)

    def test_needs_action_shows_waiting_publication(self):
        deal = _deal(self.adv, self.blogger, Deal.Status.WAITING_PUBLICATION)
        r = self.client.get(reverse("web:blogger_dashboard"))
        self.assertContains(r, "Требует действия")
        self.assertContains(r, "опубликуйте и пришлите ссылку")
        self.assertContains(r, f'href="{reverse("web:deal_detail", args=[deal.pk])}"')

    def test_needs_action_rejected_creative(self):
        _deal(self.adv, self.blogger, Deal.Status.IN_PROGRESS, creative_rejection_reason="Нет хэштегов")
        self.assertContains(self.client.get(reverse("web:blogger_dashboard")), "исправьте и отправьте снова")

    def test_needs_action_hides_others(self):
        other = _user(User.Role.BLOGGER)
        _deal(self.adv, other, Deal.Status.WAITING_PUBLICATION)
        r = self.client.get(reverse("web:blogger_dashboard"))
        self.assertNotContains(r, "Требует действия")

    def test_active_deal_rows_are_links(self):
        deal = _deal(self.adv, self.blogger, Deal.Status.CHECKING)
        r = self.client.get(reverse("web:blogger_dashboard"))
        self.assertContains(r, f'href="{reverse("web:deal_detail", args=[deal.pk])}"')
        self.assertContains(r, "На проверке")
        self.assertContains(r, "150 000")


class AdvertiserDashboardTest(TestCase):
    def setUp(self):
        self.adv = _user(User.Role.ADVERTISER)
        self.blogger = _user(User.Role.BLOGGER)
        self.client.force_login(self.adv)

    def test_needs_action_deal_and_pending_responses(self):
        deal = _deal(self.adv, self.blogger, Deal.Status.ON_APPROVAL)
        campaign = _campaign(self.adv, name="С откликами")
        CampaignResponse.objects.create(
            blogger=self.blogger, campaign=campaign, platform=_platform(self.blogger), content_type="post",
        )
        r = self.client.get(reverse("web:advertiser_dashboard"))
        self.assertContains(r, "согласуйте или отклоните")
        self.assertContains(r, f'href="{reverse("web:deal_detail", args=[deal.pk])}"')
        self.assertContains(r, "Откликов ждут решения: 1")
        self.assertContains(r, f'href="{reverse("web:campaign_detail", args=[campaign.pk])}"')

    def test_needs_action_hides_others(self):
        other = _user(User.Role.ADVERTISER)
        _deal(other, self.blogger, Deal.Status.CHECKING)
        self.assertNotContains(self.client.get(reverse("web:advertiser_dashboard")), "Требует действия")


class DealListFilterTest(TestCase):
    def setUp(self):
        self.adv = _user(User.Role.ADVERTISER)
        self.blogger = _user(User.Role.BLOGGER)
        self.done = _deal(self.adv, self.blogger, Deal.Status.COMPLETED)
        self.work = _deal(self.adv, self.blogger, Deal.Status.IN_PROGRESS)
        self.cancelled = _deal(self.adv, self.blogger, Deal.Status.CANCELLED)
        self.client.force_login(self.blogger)

    def _pks(self, query=""):
        r = self.client.get(reverse("web:deal_list") + query)
        return {d.pk for d in r.context["deals"]}

    def test_completed(self):
        self.assertEqual(self._pks("?status=completed"), {self.done.pk})

    def test_active(self):
        self.assertEqual(self._pks("?status=active"), {self.work.pk})

    def test_unknown_ignored(self):
        self.assertEqual(self._pks("?status=bogus"), {self.done.pk, self.work.pk, self.cancelled.pk})

    def test_row_is_link(self):
        r = self.client.get(reverse("web:deal_list"))
        self.assertContains(r, f'<a href="{reverse("web:deal_detail", args=[self.work.pk])}" class="px-6 py-4')


class MyResponsesTest(TestCase):
    def setUp(self):
        self.adv = _user(User.Role.ADVERTISER)
        self.blogger = _user(User.Role.BLOGGER)
        self.mine = CampaignResponse.objects.create(
            blogger=self.blogger, campaign=_campaign(self.adv, name="Моя кампания"),
            platform=_platform(self.blogger), content_type="post", status=CampaignResponse.Status.REJECTED,
        )
        other = _user(User.Role.BLOGGER)
        CampaignResponse.objects.create(
            blogger=other, campaign=_campaign(self.adv, name="Чужая кампания"),
            platform=_platform(other), content_type="post",
        )

    def test_blogger_sees_own_only(self):
        self.client.force_login(self.blogger)
        r = self.client.get(reverse("web:my_responses"))
        self.assertContains(r, "Моя кампания")
        self.assertContains(r, "Отклонён")
        self.assertNotContains(r, "Чужая кампания")

    def test_advertiser_redirected(self):
        self.client.force_login(self.adv)
        r = self.client.get(reverse("web:my_responses"))
        self.assertEqual(r.status_code, 302)


class ResponseCardTest(TestCase):
    def setUp(self):
        self.adv = _user(User.Role.ADVERTISER)
        self.campaign = _campaign(self.adv)
        self.client.force_login(self.adv)

    def _respond(self, nickname):
        blogger = _user(User.Role.BLOGGER)
        BloggerProfile.objects.update_or_create(
            user=blogger, defaults={"nickname": nickname, "rating": Decimal("4.80"), "deals_count": 7},
        )
        CampaignResponse.objects.create(
            blogger=blogger, campaign=self.campaign, platform=_platform(blogger), content_type="post",
        )

    def test_blogger_data_shown(self):
        self._respond("super_blogger")
        r = self.client.get(reverse("web:campaign_detail", args=[self.campaign.pk]))
        self.assertContains(r, "super_blogger")
        self.assertContains(r, "Instagram")
        self.assertContains(r, "125 000")
        self.assertContains(r, "3,5%")
        self.assertContains(r, "★ 4,80")
        self.assertContains(r, "завершённых сделок: 7")

    def test_queries_do_not_grow_with_responses(self):
        self._respond("b1")
        url = reverse("web:campaign_detail", args=[self.campaign.pk])
        with CaptureQueriesContext(connection) as one:
            self.client.get(url)
        self._respond("b2")
        self._respond("b3")
        with CaptureQueriesContext(connection) as three:
            self.client.get(url)
        self.assertEqual(len(one), len(three))


class ResponsePriceTest(TestCase):
    def setUp(self):
        self.adv = _user(User.Role.ADVERTISER)
        self.blogger = _user(User.Role.BLOGGER)
        self.platform = _platform(self.blogger)
        self.campaign = _campaign(self.adv)
        self.client.force_login(self.blogger)
        self.url = reverse("web:campaign_respond", args=[self.campaign.pk])

    def _post(self, price):
        return self.client.post(self.url, {
            "platform": self.platform.pk, "content_type": "post", "proposed_price": price, "message": "",
        })

    def test_spaced_price_parsed(self):
        self._post("150 000")
        self.assertEqual(CampaignResponse.objects.get(blogger=self.blogger).proposed_price, Decimal("150000"))

    def test_empty_price_means_campaign_price(self):
        self._post("")
        self.assertIsNone(CampaignResponse.objects.get(blogger=self.blogger).proposed_price)

    def test_invalid_price_no_500(self):
        for bad in ("abc", "-5"):
            r = self._post(bad)
            self.assertEqual(r.status_code, 302)
            self.assertFalse(CampaignResponse.objects.filter(blogger=self.blogger).exists())

    def test_form_field_is_spaced_input(self):
        r = self.client.get(reverse("web:campaign_detail", args=[self.campaign.pk]))
        # Поле на общем шаблоне числового поля (QA camp_test_3): пробелы между разрядами и кнопки −/+ с шагом 10 000.
        html = r.content.decode()
        field = html[html.index('name="proposed_price"') - 300:html.index('name="proposed_price"') + 300]
        self.assertIn("data-number-input", field)
        self.assertIn('data-step="10000"', field)
        self.assertContains(r, "js/number-input.js")


class NotificationTargetsTest(TestCase):
    def test_response_rejected_and_direct_offer_have_urls(self):
        adv = _user(User.Role.ADVERTISER)
        blogger = _user(User.Role.BLOGGER)
        campaign = _campaign(adv)
        NotificationService.notify_response_rejected(blogger, campaign)
        NotificationService.notify_direct_offer_received(blogger, campaign, adv)
        rejected = Notification.objects.get(user=blogger, type=Notification.Type.RESPONSE_REJECTED)
        offer = Notification.objects.get(user=blogger, type=Notification.Type.DIRECT_OFFER_RECEIVED)
        self.assertEqual(rejected.target_url, reverse("web:my_responses"))
        self.assertEqual(offer.target_url, reverse("web:blogger_dashboard"))
