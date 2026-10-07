"""
QA camp_test_3 (07.10.2026), шаг 14: сумма вывода — с разрядами и кнопками −/+; номер карты — только цифры, 16 штук,
группами по 4; запрет вывода для демо-аккаунта виден сразу.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.billing.models import Wallet, WithdrawalRequest
from apps.users.models import User
from apps.web.views.billing import card_number_error


def _blogger(is_demo=False, balance="500000"):
    user = User.objects.create_user(
        email="bl@test.com", password="pass1234", role=User.Role.BLOGGER, status=User.Status.ACTIVE,
    )
    if is_demo:
        user.is_demo = True
        user.save(update_fields=["is_demo"])
    Wallet.objects.update_or_create(user=user, defaults={"available_balance": Decimal(balance)})
    return user


class CardNumberTest(TestCase):
    def test_rules(self):
        self.assertEqual(card_number_error("8600 1234 5678 9012"), (None, "8600123456789012"))
        self.assertEqual(card_number_error("8600-1234-5678-9012"), (None, "8600123456789012"))
        self.assertIsNotNone(card_number_error("8600 1234 5678 901")[0])  # 15 цифр
        self.assertIsNotNone(card_number_error("8600 1234 5678 90123")[0])  # 17 цифр
        self.assertIsNotNone(card_number_error("8600 abcd 5678 9012")[0])
        self.assertIsNotNone(card_number_error("")[0])


class WithdrawalFormTest(TestCase):
    def test_amount_with_spaces_and_card_digits_saved(self):
        user = _blogger()
        self.client.force_login(user)
        self.client.post(reverse("web:wallet"), {"amount": "100 000", "card": "8600 1234 5678 9012"})
        wr = WithdrawalRequest.objects.get(blogger=user)
        self.assertEqual(wr.amount, Decimal("100000"))
        self.assertEqual(wr.requisites["details"], "8600123456789012")

    def test_short_card_rejected_and_values_kept(self):
        user = _blogger()
        self.client.force_login(user)
        resp = self.client.post(reverse("web:wallet"), {"amount": "100 000", "card": "8600 1234"})
        self.assertFalse(WithdrawalRequest.objects.exists())
        html = resp.content.decode()
        self.assertIn("Номер карты — 16 цифр.", html)
        self.assertIn('value="8600 1234"', html)
        self.assertIn('value="100 000"', html)

    def test_form_markup(self):
        self.client.force_login(_blogger())
        html = self.client.get(reverse("web:wallet")).content.decode()
        start = html.index('name="amount"')
        self.assertIn('data-number-input', html[start - 300:start + 300])
        self.assertIn('data-step="10000"', html[start - 300:start + 300])
        self.assertIn("data-card-input", html)
        self.assertIn("js/number-input.js", html)
        self.assertIn("novalidate", html)

    def test_demo_sees_notice_and_disabled_form(self):
        self.client.force_login(_blogger(is_demo=True))
        html = self.client.get(reverse("web:wallet")).content.decode()
        self.assertIn("Вывод средств недоступен для демо-аккаунтов.", html)
        self.assertIn("<fieldset disabled", html)

    def test_regular_blogger_form_enabled(self):
        self.client.force_login(_blogger())
        html = self.client.get(reverse("web:wallet")).content.decode()
        self.assertNotIn("недоступен для демо-аккаунтов", html)
        self.assertNotIn("<fieldset disabled", html)

    def test_pending_count_text(self):
        user = _blogger()
        self.client.force_login(user)
        self.client.post(reverse("web:wallet"), {"amount": "100000", "card": "8600123456789012"})
        html = self.client.get(reverse("web:wallet")).content.decode()
        self.assertIn("Заявок в обработке: 1", html)
