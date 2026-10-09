"""
QA camp_test_3 (07.10.2026), шаг 14 + T7/#39 (09.10.2026): сумма вывода — с разрядами и кнопками −/+;
запрет вывода для демо-аккаунта виден сразу; с 09.10 вывод работает только по подтверждённым реквизитам —
номер карты больше не вводится в самой форме вывода (см. apps.billing.payout_requisites).
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.billing.models import PayoutRequisites, Wallet, WithdrawalRequest
from apps.users.models import User


def _blogger(is_demo=False, balance="500000"):
    user = User.objects.create_user(
        email="bl@test.com", password="pass1234", role=User.Role.BLOGGER, status=User.Status.ACTIVE,
    )
    if is_demo:
        user.is_demo = True
        user.save(update_fields=["is_demo"])
    Wallet.objects.update_or_create(user=user, defaults={"available_balance": Decimal(balance)})
    return user


def _approved_card(user, number="8600123456789012", holder="Test Bloggerov"):
    return PayoutRequisites.objects.create(
        user=user, requisite_type=PayoutRequisites.RequisiteType.CARD,
        card_number=number, card_holder_name=holder,
        status=PayoutRequisites.Status.APPROVED,
    )


class WithdrawalFormTest(TestCase):
    def test_withdrawal_uses_confirmed_requisites_snapshot(self):
        user = _blogger()
        _approved_card(user)
        self.client.force_login(user)
        self.client.post(reverse("web:wallet"), {"amount": "100 000"})
        wr = WithdrawalRequest.objects.get(blogger=user)
        self.assertEqual(wr.amount, Decimal("100000"))
        self.assertEqual(wr.requisites["card_number"], "8600123456789012")

    def test_withdrawal_unavailable_without_requisites(self):
        user = _blogger()
        self.client.force_login(user)
        resp = self.client.post(reverse("web:wallet"), {"amount": "100 000"})
        self.assertFalse(WithdrawalRequest.objects.exists())
        self.assertIn("Ошибка", resp.content.decode()) if resp.status_code == 200 else None

    def test_no_requisites_message_and_no_form(self):
        self.client.force_login(_blogger())
        html = self.client.get(reverse("web:wallet")).content.decode()
        self.assertIn("подтвердите реквизиты выплаты", html)
        self.assertNotIn('name="amount"', html)

    def test_form_markup_with_confirmed_requisites(self):
        user = _blogger()
        _approved_card(user)
        self.client.force_login(user)
        html = self.client.get(reverse("web:wallet")).content.decode()
        start = html.index('name="amount"')
        self.assertIn('data-number-input', html[start - 300:start + 300])
        self.assertIn('data-step="10000"', html[start - 300:start + 300])
        self.assertIn("novalidate", html)
        self.assertNotIn('name="card_number"', html)

    def test_demo_sees_notice_and_no_form(self):
        user = _blogger(is_demo=True)
        _approved_card(user)
        self.client.force_login(user)
        html = self.client.get(reverse("web:wallet")).content.decode()
        self.assertIn("Вывод средств недоступен для демо-аккаунтов.", html)
        self.assertNotIn('name="amount"', html)

    def test_regular_blogger_form_enabled(self):
        user = _blogger()
        _approved_card(user)
        self.client.force_login(user)
        html = self.client.get(reverse("web:wallet")).content.decode()
        self.assertNotIn("недоступен для демо-аккаунтов", html)

    def test_pending_count_text(self):
        user = _blogger()
        _approved_card(user)
        self.client.force_login(user)
        self.client.post(reverse("web:wallet"), {"amount": "100000"})
        html = self.client.get(reverse("web:wallet")).content.decode()
        self.assertIn("Заявок в обработке: 1", html)

    def test_changing_requisites_does_not_affect_existing_withdrawal_snapshot(self):
        user = _blogger()
        old = _approved_card(user, number="1111222233334444")
        self.client.force_login(user)
        self.client.post(reverse("web:wallet"), {"amount": "100000"})
        wr = WithdrawalRequest.objects.get(blogger=user)
        self.assertEqual(wr.requisites["card_number"], "1111222233334444")

        old.status = PayoutRequisites.Status.PENDING
        old.save(update_fields=["status"])
        _approved_card(user, number="9999888877776666")

        wr.refresh_from_db()
        self.assertEqual(wr.requisites["card_number"], "1111222233334444")
