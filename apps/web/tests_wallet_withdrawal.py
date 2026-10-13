"""
#60 (13.10.2026): у исполнителя нет формы вывода — выплаты формируются по сделкам. Кошелёк показывает
«К выплате», «Выплачено», список выплат с причинами блокировки; остаток до перехода — отдельной карточкой.
История: QA camp_test_3 шаг 14 и T7/#39 (сумма с разрядами, реквизиты снимком) — форма убрана.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.billing.models import PayoutRequisites, Wallet, WithdrawalRequest
from apps.billing.services import BillingService
from apps.users.models import User


def _blogger(is_demo=False, balance="0"):
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


def _payout(user, amount="100000", requisites=None):
    Wallet.objects.filter(user=user).update(available_balance=Decimal(amount))
    wr = WithdrawalRequest.objects.create(blogger=user, amount=Decimal(amount), requisites=requisites or {})
    BillingService.process_withdrawal(wr)
    return wr


class WalletPayoutsTest(TestCase):
    def test_no_withdrawal_form_and_post_ignored(self):
        user = _blogger(balance="500000")
        _approved_card(user)
        self.client.force_login(user)
        html = self.client.get(reverse("web:wallet")).content.decode()
        self.assertNotIn('name="amount"', html)
        self.assertNotIn("number-input.js", html)
        self.client.post(reverse("web:wallet"), {"amount": "100 000"})
        self.assertFalse(WithdrawalRequest.objects.exists())

    def test_cards_to_pay_and_paid_out(self):
        user = _blogger()
        wr = _payout(user, "150000", {"type": "card", "card_number": "8600"})
        self.client.force_login(user)
        html = self.client.get(reverse("web:wallet")).content.decode()
        self.assertIn("К выплате", html)
        self.assertIn("150 000", html)
        self.assertIn("в реестре — ждёт проведения", html)
        self.assertNotIn("data-legacy-available", html)
        BillingService.complete_withdrawal(wr, comment="п/п 1")
        html = self.client.get(reverse("web:wallet")).content.decode()
        self.assertIn("Выплачено", html)
        self.assertIn("проведена", html)

    def test_blocked_reason_links_to_requisites(self):
        user = _blogger()
        _payout(user)
        self.client.force_login(user)
        html = self.client.get(reverse("web:wallet")).content.decode()
        self.assertIn("нет подтверждённых реквизитов", html)
        self.assertIn(reverse("web:payout_requisites"), html)
        self.assertIn("data-no-requisites", html)

    def test_legacy_available_card_only_when_positive(self):
        user = _blogger(balance="70000")
        self.client.force_login(user)
        html = self.client.get(reverse("web:wallet")).content.decode()
        self.assertIn("data-legacy-available", html)
        self.assertIn("70 000", html)

    def test_demo_blogger_sees_same_wallet(self):
        user = _blogger(is_demo=True)
        self.client.force_login(user)
        html = self.client.get(reverse("web:wallet")).content.decode()
        self.assertIn("data-blogger-cards", html)
        self.assertNotIn("недоступен для демо-аккаунтов", html)

    def test_api_withdrawals_read_only(self):
        from rest_framework.test import APIClient

        user = _blogger()
        _payout(user)
        api = APIClient()
        api.force_authenticate(user)
        r = api.get(reverse("billing:withdrawal-list"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["results"][0]["blocked_reasons"], ["нет подтверждённых реквизитов"])
        self.assertEqual(api.post(reverse("billing:withdrawal-list"), {"amount": "1"}, format="json").status_code, 405)
