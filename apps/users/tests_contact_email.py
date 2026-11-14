"""Адрес для писем (Р27): у входа по ИНН и по телефону логин служебный — письма идут на подтверждённый адрес из профиля."""
from decimal import Decimal

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.billing import topups
from apps.notifications import emails
from apps.notifications.tests_emails import OPERATOR, SITE, SentMail, _staff
from apps.users import contact_email, data_requests
from apps.users.models import DataRequest, EmailConfirmationToken, User

LEGAL = "legal.305123456@ddocs.internal"


def _legal_advertiser():
    user = User.objects.create_user(email=LEGAL, password="x", role=User.Role.ADVERTISER,
                                    status=User.Status.ACTIVE, is_email_confirmed=True)
    user.advertiser_profile.inn = "305123456"
    user.advertiser_profile.save()
    return user


@override_settings(**SITE, **OPERATOR)
class ContactEmailTests(TestCase):
    def setUp(self):
        self.user = _legal_advertiser()
        self.client.force_login(self.user)

    def _request(self, address="boss@company.uz"):
        with SentMail(self):
            return self.client.post(reverse("web:profile_contact_email"), {"email": address})

    def _link(self, message):
        token = EmailConfirmationToken.objects.filter(user=self.user, is_used=False).exclude(email="").get()
        url = reverse("web:email_confirm", args=[token.token])
        self.assertIn("https://ublogers.uz" + url, message.body)
        return url

    def _confirm(self, url):
        with SentMail(self):
            return self.client.get(url)

    def test_service_login_and_demo_have_no_address(self):
        self.assertEqual(emails.address_for(self.user), "")
        demo = User.objects.create_user(email="advertiser@demo.com", password="x", role=User.Role.ADVERTISER, is_demo=True)
        self.assertEqual(emails.address_for(demo), "")
        real = User.objects.create_user(email="me@example.com", password="x", role=User.Role.BLOGGER)
        self.assertEqual(emails.address_for(real), "me@example.com")

    def test_profile_asks_for_address(self):
        page = self.client.get(reverse("web:profile"))
        self.assertContains(page, "data-contact-email")
        self.assertContains(page, "Укажите адрес почты")

    def test_confirm_then_welcome_and_money_letters_go_to_it(self):
        self._request()
        self.assertEqual([m.to for m in mail.outbox], [["boss@company.uz"]])
        self.assertEqual(mail.outbox[0].subject, "Подтвердите адрес для писем — UBlogers!")
        url = self._link(mail.outbox[0])
        self.user.refresh_from_db()
        self.assertEqual(self.user.contact_email, "")
        self.assertContains(self.client.get(reverse("web:profile")), "Ждём подтверждения")

        mail.outbox.clear()
        page = self._confirm(url)
        self.assertContains(page, "Адрес подтверждён")
        self.user.refresh_from_db()
        self.assertEqual(self.user.contact_email, "boss@company.uz")
        self.assertEqual(self.user.email, LEGAL)  # логин по ИНН не меняется
        self.assertEqual([(m.to, m.subject) for m in mail.outbox],
                         [(["boss@company.uz"], "Добро пожаловать в UBlogers!")])
        self.assertContains(self._confirm(url), "Ссылка недействительна")

        mail.outbox.clear()
        with SentMail(self):
            topups.create_invoice(self.user, Decimal("1000000"))
            self.client.post(reverse("web:support"), {"name": "Иван", "email": "x@y.uz", "message": "Вопрос"})
        self.assertEqual([m.to for m in mail.outbox], [["boss@company.uz"], ["boss@company.uz"]])

    def test_new_address_works_only_after_confirmation_and_no_second_welcome(self):
        self._request()
        self._confirm(self._link(mail.outbox[0]))
        mail.outbox.clear()
        self._request("new@company.uz")
        self.assertEqual(emails.address_for(User.objects.get(pk=self.user.pk)), "boss@company.uz")
        url = self._link(mail.outbox[0])
        mail.outbox.clear()
        self._confirm(url)
        self.assertEqual(mail.outbox, [])
        self.assertEqual(User.objects.get(pk=self.user.pk).contact_email, "new@company.uz")

    def test_only_last_link_is_valid(self):
        self._request("one@company.uz")
        self._request("two@company.uz")
        self.assertEqual(contact_email.pending(self.user), "two@company.uz")
        self.assertEqual(EmailConfirmationToken.objects.filter(user=self.user, is_used=False).count(), 1)

    def test_rejects_service_and_same_address(self):
        self._request("blogger.998901234567@sms.internal")
        self.assertEqual(mail.outbox, [])
        self._request()
        self._confirm(self._link(mail.outbox[0]))
        with self.assertRaisesMessage(contact_email.ContactEmailError, "уже подтверждён"):
            contact_email.request_change(User.objects.get(pk=self.user.pk), "Boss@Company.uz")

    def test_remove(self):
        self._request()
        self._confirm(self._link(mail.outbox[0]))
        self.client.post(reverse("web:profile_contact_email"), {"action": "remove"})
        self.assertEqual(emails.address_for(User.objects.get(pk=self.user.pk)), "")

    def test_shown_in_my_data_and_cleared_on_closing(self):
        self._request()
        self._confirm(self._link(mail.outbox[0]))
        self.assertContains(self.client.get(reverse("web:my_data")), "boss@company.uz")
        request = data_requests.create_request(self.user, DataRequest.Kind.CLOSE)
        data_requests.close_account(request.pk, _staff())
        self.assertEqual(User.objects.get(pk=self.user.pk).contact_email, "")

    def test_login_confirmation_unchanged(self):
        """Старое подтверждение логина (API-регистрация) по-прежнему активирует аккаунт."""
        user = User.objects.create_user(email="api@example.com", password="x", role=User.Role.BLOGGER)
        from django.utils import timezone
        token = EmailConfirmationToken.objects.create(user=user, expires_at=timezone.now() + timezone.timedelta(hours=1))
        self.client.logout()
        with SentMail(self):
            self.client.get(reverse("web:email_confirm", args=[token.token]))
        user.refresh_from_db()
        self.assertTrue(user.is_email_confirmed)
        self.assertEqual(user.contact_email, "")
