"""
QA camp_test_2 (06.10.2026): уведомление гаснет, когда адресат открыл страницу, на которую оно ведёт;
события «публикация добавлена» и «кампания на модерации»; переходы через API уведомляют так же, как сайт.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.billing.models import Wallet
from apps.campaigns.models import Campaign
from apps.campaigns.models import Response as CampaignResponse
from apps.deals.models import Deal
from apps.notifications.models import Notification
from apps.notifications.service import NotificationService
from apps.platforms.models import Platform
from apps.users.models import User

T = Notification.Type


def _user(email, role=User.Role.ADVERTISER, is_staff=False):
    return User.objects.create_user(
        email=email, password="Test1234!", role=role, status=User.Status.ACTIVE, is_staff=is_staff,
    )


def _campaign(advertiser, status=Campaign.Status.ACTIVE, name="Кампания"):
    today = timezone.localdate()
    return Campaign.objects.create(
        advertiser=advertiser, name=name, description="desc",
        payment_type=Campaign.PaymentType.FIXED, fixed_price=Decimal("50000"),
        budget=Decimal("500000"), status=status,
        start_date=today, end_date=today + timedelta(days=30),  # даты обязательны для модерации (Р4)
    )


def _platform(blogger):
    return Platform.objects.create(
        blogger=blogger, social_type=Platform.SocialType.INSTAGRAM,
        url=f"https://instagram.com/{blogger.pk}", subscribers=10000,
        price_post=Decimal("50000"), status=Platform.Status.APPROVED,
    )


def _deal(campaign, blogger, platform, status=Deal.Status.IN_PROGRESS):
    return Deal.objects.create(
        campaign=campaign, blogger=blogger, advertiser=campaign.advertiser,
        platform=platform, amount=Decimal("50000"), status=status,
    )


def _fund(user, available="0", reserved="0"):
    Wallet.objects.update_or_create(
        user=user, defaults={"available_balance": Decimal(available), "reserved_balance": Decimal(reserved)},
    )


def _unread(user, **filters):
    return Notification.objects.filter(user=user, is_read=False, **filters)


class ResolveOnOpenTest(TestCase):
    """Открытие целевой страницы гасит уведомление — независимо от того, как пользователь туда пришёл."""

    def setUp(self):
        self.adv = _user("adv@test.com")
        self.blogger = _user("bl@test.com", User.Role.BLOGGER)
        self.campaign = _campaign(self.adv)
        self.deal = _deal(self.campaign, self.blogger, _platform(self.blogger), Deal.Status.ON_APPROVAL)

    def test_opening_deal_resolves_deal_notification(self):
        NotificationService.notify_creative_submitted(self.adv, self.deal)
        self.client.force_login(self.adv)
        self.client.get(reverse("web:deal_detail", kwargs={"pk": self.deal.pk}))
        self.assertFalse(_unread(self.adv).exists())

    def test_badge_counter_drops_on_the_same_page(self):
        # QA camp_test_3: счётчик на открытой странице уже без этого уведомления, а не на следующей
        NotificationService.notify_creative_submitted(self.adv, self.deal)
        self.client.force_login(self.adv)
        resp = self.client.get(reverse("web:deal_detail", kwargs={"pk": self.deal.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["unread_notifications_count"], 0)

    def test_opening_campaign_resolves_only_that_campaign(self):
        other = _campaign(self.adv, name="Другая")
        NotificationService.notify_campaign_changes_proposed(self.adv, self.campaign)
        NotificationService.notify_campaign_changes_proposed(self.adv, other)
        self.client.force_login(self.adv)
        self.client.get(reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk}))
        unread = _unread(self.adv)
        self.assertEqual(unread.count(), 1)
        self.assertEqual(unread.get().url, reverse("web:campaign_detail", kwargs={"pk": other.pk}))

    def test_other_deal_does_not_resolve(self):
        other_deal = _deal(_campaign(self.adv, name="Другая"), self.blogger, self.deal.platform)
        NotificationService.notify_creative_submitted(self.adv, self.deal)
        self.client.force_login(self.adv)
        self.client.get(reverse("web:deal_detail", kwargs={"pk": other_deal.pk}))
        self.assertTrue(_unread(self.adv).exists())

    def test_foreign_deal_404_changes_nothing(self):
        stranger = _user("x@test.com")
        NotificationService.notify_creative_submitted(self.adv, self.deal)
        Notification.objects.create(
            user=stranger, type=T.SYSTEM, title="t", body="b", related_deal=self.deal,
        )
        self.client.force_login(stranger)
        resp = self.client.get(reverse("web:deal_detail", kwargs={"pk": self.deal.pk}))
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(_unread(stranger).exists())
        self.assertTrue(_unread(self.adv).exists())

    def test_post_does_not_resolve(self):
        NotificationService.notify_creative_submitted(self.adv, self.deal)
        self.client.force_login(self.adv)
        self.client.post(reverse("web:deal_send_message", kwargs={"pk": self.deal.pk}), {"text": "hi"})
        self.assertTrue(_unread(self.adv).exists())

    def test_other_users_notifications_untouched(self):
        NotificationService.notify_creative_submitted(self.adv, self.deal)
        NotificationService.notify_response_accepted(self.blogger, self.campaign, self.deal)
        self.client.force_login(self.adv)
        self.client.get(reverse("web:deal_detail", kwargs={"pk": self.deal.pk}))
        self.assertTrue(_unread(self.blogger).exists())


class NewEventsTest(TestCase):
    def setUp(self):
        self.adv = _user("adv@test.com")
        self.adv.is_demo = True
        self.adv.save(update_fields=["is_demo"])
        self.blogger = _user("bl@test.com", User.Role.BLOGGER)
        self.staff1 = _user("s1@test.com", is_staff=True)
        self.staff2 = _user("s2@test.com", is_staff=True)
        self.inactive_staff = _user("s3@test.com", is_staff=True)
        self.inactive_staff.is_active = False
        self.inactive_staff.save(update_fields=["is_active"])

    def test_publication_notifies_advertiser(self):
        deal = _deal(_campaign(self.adv), self.blogger, _platform(self.blogger), Deal.Status.WAITING_PUBLICATION)
        self.client.force_login(self.blogger)
        self.client.post(
            reverse("web:deal_submit_publication", kwargs={"pk": deal.pk}),
            {"publication_url": "https://instagram.com/p/1"},
        )
        n = _unread(self.adv, type=T.PUBLICATION_SUBMITTED).get()
        self.assertEqual(n.target_url, reverse("web:deal_detail", kwargs={"pk": deal.pk}))

    def test_submit_draft_notifies_active_staff(self):
        campaign = _campaign(self.adv, Campaign.Status.DRAFT)
        self.client.force_login(self.adv)
        self.client.post(reverse("web:campaign_submit", kwargs={"pk": campaign.pk}))
        notified = set(
            Notification.objects.filter(type=T.CAMPAIGN_MODERATION_REQUESTED).values_list("user__email", flat=True)
        )
        self.assertEqual(notified, {"s1@test.com", "s2@test.com"})
        n = Notification.objects.filter(user=self.staff1).get()
        self.assertEqual(n.title, "Новая кампания на модерации")
        self.assertEqual(n.url, reverse("web:admin_campaign_detail", kwargs={"pk": campaign.pk}))

    def test_resubmit_after_rejection_has_own_title(self):
        campaign = _campaign(self.adv, Campaign.Status.REJECTED)
        self.client.force_login(self.adv)
        self.client.post(reverse("web:campaign_submit", kwargs={"pk": campaign.pk}))
        self.assertEqual(
            Notification.objects.get(user=self.staff1).title,
            "Кампания повторно на модерации после отклонения",
        )

    def test_staff_opening_card_resolves_only_own(self):
        campaign = _campaign(self.adv, Campaign.Status.DRAFT)
        self.client.force_login(self.adv)
        self.client.post(reverse("web:campaign_submit", kwargs={"pk": campaign.pk}))
        self.client.force_login(self.staff1)
        self.client.get(reverse("web:admin_campaign_detail", kwargs={"pk": campaign.pk}))
        self.assertFalse(_unread(self.staff1).exists())
        self.assertTrue(_unread(self.staff2).exists())


class ApiParityTest(TestCase):
    """Переход через API создаёт то же уведомление, что и на сайте."""

    def setUp(self):
        self.adv = _user("adv@test.com")
        self.adv.is_demo = True
        self.adv.save(update_fields=["is_demo"])
        self.blogger = _user("bl@test.com", User.Role.BLOGGER)
        self.staff = _user("s@test.com", is_staff=True)
        self.platform = _platform(self.blogger)
        self.campaign = _campaign(self.adv)
        self.api = APIClient()

    def _deal_action(self, user, deal, action, data=None):
        self.api.force_authenticate(user)
        return self.api.post(reverse(f"deals:deal-{action}", kwargs={"pk": deal.pk}), data or {})

    def test_accept_response_notifies_blogger(self):
        _fund(self.adv, available="500000")
        resp_obj = CampaignResponse.objects.create(
            campaign=self.campaign, blogger=self.blogger, platform=self.platform,
        )
        self.api.force_authenticate(self.adv)
        r = self.api.post(reverse("campaigns:response-accept", kwargs={"pk": resp_obj.pk}),
                          {"publication_date": timezone.localdate().isoformat()}, format="json")
        self.assertEqual(r.status_code, 201)
        # Принятие отклика направляет оферту — то же уведомление, что на сайте (#33).
        self.assertTrue(_unread(self.blogger, type=T.DIRECT_OFFER_RECEIVED, title="Вам направлена оферта").exists())

    def test_reject_response_notifies_blogger(self):
        resp_obj = CampaignResponse.objects.create(
            campaign=self.campaign, blogger=self.blogger, platform=self.platform,
        )
        self.api.force_authenticate(self.adv)
        self.api.post(reverse("campaigns:response-reject", kwargs={"pk": resp_obj.pk}))
        self.assertTrue(_unread(self.blogger, type=T.RESPONSE_REJECTED).exists())

    def test_creative_cycle_notifies_both_sides(self):
        deal = _deal(self.campaign, self.blogger, self.platform)
        self._deal_action(self.blogger, deal, "submit-creative", {"creative_text": "текст"})
        self.assertTrue(_unread(self.adv, type=T.CREATIVE_SUBMITTED).exists())
        self._deal_action(self.adv, deal, "reject-creative", {"reason": "переделать"})
        self.assertTrue(_unread(self.blogger, type=T.CREATIVE_REJECTED).exists())
        self._deal_action(self.blogger, deal, "submit-creative", {"creative_text": "новый"})
        self._deal_action(self.adv, deal, "approve-creative")
        self.assertTrue(_unread(self.blogger, type=T.CREATIVE_APPROVED).exists())

    def test_publication_and_completion(self):
        _fund(self.adv, reserved="50000")
        deal = _deal(self.campaign, self.blogger, self.platform, Deal.Status.WAITING_PUBLICATION)
        self._deal_action(self.blogger, deal, "submit-publication", {"publication_url": "https://instagram.com/p/1"})
        self.assertTrue(_unread(self.adv, type=T.PUBLICATION_SUBMITTED).exists())
        self._deal_action(self.adv, deal, "confirm-publication")
        self.assertTrue(_unread(self.blogger, type=T.PAYMENT_RECEIVED).exists())

    def test_cancel_notifies_other_side(self):
        _fund(self.adv, reserved="50000")
        deal = _deal(self.campaign, self.blogger, self.platform)
        self._deal_action(self.adv, deal, "cancel")
        self.assertTrue(_unread(self.blogger, type=T.DEAL_CANCELLED).exists())
        self.assertFalse(_unread(self.adv, type=T.DEAL_CANCELLED).exists())

    def test_submit_for_moderation_notifies_staff(self):
        campaign = _campaign(self.adv, Campaign.Status.DRAFT)
        self.api.force_authenticate(self.adv)
        self.api.post(reverse("campaigns:campaign-submit-for-moderation", kwargs={"pk": campaign.pk}))
        self.assertTrue(_unread(self.staff, type=T.CAMPAIGN_MODERATION_REQUESTED).exists())


class SamePageBadgeQA3Test(TestCase):
    """QA camp_test_3, шаги 4.1, 6.1, 9.2, 10.1, 10.4: значок гаснет на той же странице, которую открыли."""

    def setUp(self):
        self.adv = _user("adv@test.com")
        self.blogger = _user("bl@test.com", User.Role.BLOGGER)
        self.campaign = _campaign(self.adv)
        self.deal = _deal(self.campaign, self.blogger, _platform(self.blogger))
        self.campaign_url = reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk})
        self.deal_url = reverse("web:deal_detail", kwargs={"pk": self.deal.pk})

    def _count_on(self, user, url):
        self.client.force_login(user)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        return resp.context["unread_notifications_count"]

    def test_proposed_edits_on_campaign_page(self):  # 4.1
        NotificationService.notify_campaign_changes_proposed(self.adv, self.campaign)
        self.assertEqual(self._count_on(self.adv, self.campaign_url), 0)

    def test_new_response_on_campaign_page(self):  # 6.1
        NotificationService.notify_new_response(self.adv, self.campaign, self.blogger)
        self.assertEqual(self._count_on(self.adv, self.campaign_url), 0)

    def test_response_accepted_on_deal_page(self):  # 9.2
        NotificationService.notify_response_accepted(self.blogger, self.campaign, self.deal)
        self.assertEqual(self._count_on(self.blogger, self.deal_url), 0)

    def test_creative_submitted_on_deal_page(self):  # 10.1
        NotificationService.notify_creative_submitted(self.adv, self.deal)
        self.assertEqual(self._count_on(self.adv, self.deal_url), 0)

    def test_payment_received_resolves_in_wallet(self):  # 10.4
        NotificationService.notify_deal_completed(self.blogger, self.deal)
        note = Notification.objects.get(user=self.blogger, type=T.PAYMENT_RECEIVED)
        self.assertEqual(note.target_url, reverse("web:wallet"))
        self.assertEqual(self._count_on(self.blogger, reverse("web:wallet")), 0)

    def test_payment_received_also_resolves_on_deal_page(self):
        NotificationService.notify_deal_completed(self.blogger, self.deal)
        self.assertEqual(self._count_on(self.blogger, self.deal_url), 0)

    def test_response_rejected_resolves_on_campaign_page(self):
        NotificationService.notify_response_rejected(self.blogger, self.campaign, "Готовы на 150 000")
        note = Notification.objects.get(user=self.blogger, type=T.RESPONSE_REJECTED)
        self.assertEqual(note.target_url, reverse("web:my_responses"))
        self.assertEqual(self._count_on(self.blogger, self.campaign_url), 0)

    def test_response_rejected_other_campaign_untouched(self):
        other = _campaign(self.adv, name="Другая")
        NotificationService.notify_response_rejected(self.blogger, other)
        self.assertEqual(self._count_on(self.blogger, self.campaign_url), 1)

    def test_direct_offer_rejected_leads_to_campaign(self):
        NotificationService.notify_direct_offer_rejected(self.adv, self.campaign, self.blogger)
        note = Notification.objects.get(user=self.adv, type=T.DIRECT_OFFER_REJECTED)
        self.assertEqual(note.target_url, self.campaign_url)
        self.assertEqual(self._count_on(self.adv, self.campaign_url), 0)

    def test_failed_page_restores_unread(self):
        # блогер не видит неактивную чужую кампанию (404) — уведомление о ней остаётся непрочитанным
        self.campaign.status = Campaign.Status.PAUSED
        self.campaign.save(update_fields=["status"])
        NotificationService.notify_response_rejected(self.blogger, self.campaign)
        self.client.force_login(self.blogger)
        self.assertEqual(self.client.get(self.campaign_url).status_code, 404)
        self.assertTrue(_unread(self.blogger, type=T.RESPONSE_REJECTED).exists())
