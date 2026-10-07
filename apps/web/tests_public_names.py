"""
QA camp_test_3 (07.10.2026), шаг 9.3: вместо email второй стороны — компания или ник. Email видит только сотрудник:
по нему стороны договорились бы в обход площадки.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.billing.models import Wallet
from apps.campaigns.models import Campaign
from apps.deals.models import Deal, DealStatusLog
from apps.deals.services import cancel
from apps.notifications.models import Notification
from apps.notifications.service import NotificationService
from apps.platforms.models import Platform
from apps.users.models import User


def _user(email, role, is_staff=False):
    return User.objects.create_user(
        email=email, password="pass1234", role=role, status=User.Status.ACTIVE, is_staff=is_staff,
    )


class PublicNameTest(TestCase):
    def test_advertiser_company_then_contact_then_role(self):
        adv = _user("adv@test.com", User.Role.ADVERTISER)
        self.assertEqual(adv.public_name, "Рекламодатель")
        adv.advertiser_profile.contact_name = "Анна"
        adv.advertiser_profile.save()
        self.assertEqual(User.objects.get(pk=adv.pk).public_name, "Анна")
        adv.advertiser_profile.company_name = "Цветочный магазин"
        adv.advertiser_profile.save()
        self.assertEqual(User.objects.get(pk=adv.pk).public_name, "Цветочный магазин")

    def test_blogger_nickname_then_role(self):
        bl = _user("bl@test.com", User.Role.BLOGGER)
        self.assertEqual(bl.public_name, "Блогер")
        bl.blogger_profile.nickname = "flowers_tashkent"
        bl.blogger_profile.save()
        self.assertEqual(User.objects.get(pk=bl.pk).public_name, "flowers_tashkent")


class DealPagesHideEmailsTest(TestCase):
    def setUp(self):
        self.adv = _user("secret-adv@test.com", User.Role.ADVERTISER)
        self.adv.advertiser_profile.company_name = "Цветочный магазин"
        self.adv.advertiser_profile.save()
        self.blogger = _user("secret-bl@test.com", User.Role.BLOGGER)
        self.blogger.blogger_profile.nickname = "flowers_tashkent"
        self.blogger.blogger_profile.save()
        self.staff = _user("staff@test.com", User.Role.ADVERTISER, is_staff=True)
        campaign = Campaign.objects.create(
            advertiser=self.adv, name="Кампания", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("300000"), status=Campaign.Status.ACTIVE,
        )
        platform = Platform.objects.create(
            blogger=self.blogger, social_type=Platform.SocialType.INSTAGRAM, url="https://instagram.com/b",
            subscribers=12000, price_post=Decimal("50000"), status=Platform.Status.APPROVED,
        )
        self.deal = Deal.objects.create(
            campaign=campaign, blogger=self.blogger, advertiser=self.adv, platform=platform,
            amount=Decimal("150000"), status=Deal.Status.IN_PROGRESS,
        )
        self.deal.messages.create(sender=self.adv, text="Привет")
        self.deal.messages.create(sender=self.blogger, text="Здравствуйте")

    def _page(self, user, name):
        self.client.force_login(user)
        return self.client.get(reverse(name, **({"kwargs": {"pk": self.deal.pk}} if name == "web:deal_detail" else {})))

    def test_blogger_sees_company_not_email(self):
        for name in ("web:deal_detail", "web:deal_list"):
            html = self._page(self.blogger, name).content.decode()
            self.assertIn("Цветочный магазин", html)
            self.assertNotIn("secret-adv@test.com", html)

    def test_advertiser_sees_nickname_not_email(self):
        for name in ("web:deal_detail", "web:deal_list"):
            html = self._page(self.adv, name).content.decode()
            self.assertIn("flowers_tashkent", html)
            self.assertNotIn("secret-bl@test.com", html)

    def test_staff_sees_emails(self):
        html = self._page(self.staff, "web:deal_detail").content.decode()
        self.assertIn("secret-adv@test.com", html)
        self.assertIn("secret-bl@test.com", html)

    def test_cancel_log_has_no_email(self):
        Wallet.objects.update_or_create(
            user=self.adv, defaults={"available_balance": Decimal("0"), "reserved_balance": Decimal("150000")},
        )
        cancel(self.deal.pk, self.adv)
        comment = DealStatusLog.objects.filter(deal=self.deal).latest("created_at").comment
        self.assertEqual(comment, "Отменено рекламодателем.")
        html = self._page(self.blogger, "web:deal_detail").content.decode()
        self.assertNotIn("secret-adv@test.com", html)


class NotificationTextsHideEmailsTest(TestCase):
    def test_texts_use_public_names(self):
        adv = _user("secret-adv@test.com", User.Role.ADVERTISER)
        blogger = _user("secret-bl@test.com", User.Role.BLOGGER)
        campaign = Campaign.objects.create(
            advertiser=adv, name="Кампания", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("300000"), status=Campaign.Status.ACTIVE,
        )
        NotificationService.notify_new_response(adv, campaign, blogger)
        NotificationService.notify_direct_offer_received(blogger, campaign, adv)
        NotificationService.notify_direct_offer_rejected(adv, campaign, blogger)
        for note in Notification.objects.all():
            self.assertNotIn("@", note.body, note.title)
