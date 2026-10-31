"""Пополнение по счёту на оплату и возврат свободного остатка."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.billing import metrics, topups
from apps.billing.models import BalanceRefund, PaymentInvoice, Transaction, Wallet
from apps.billing.services import BillingService
from apps.billing.topups import TopUpError
from apps.notifications.models import Notification
from apps.users import data_requests
from apps.users.models import User

ACCOUNT = "20208000900100000001"
OTHER_ACCOUNT = "20208000900100000002"
MFO = "00014"
OPERATOR = {"OPERATOR_BANK_NAME": "Тест банк", "OPERATOR_ACCOUNT": "20208000000000000099", "OPERATOR_MFO": "00001"}


def _advertiser(email="adv@test.local", inn="305123456", **extra):
    user = User.objects.create_user(email=email, password="x", role=User.Role.ADVERTISER,
                                    status=User.Status.ACTIVE, **extra)
    user.advertiser_profile.inn = inn
    user.advertiser_profile.company_name = "ООО Тест"
    user.advertiser_profile.save()
    return user


def _staff():
    return User.objects.create_user(email="st@test.local", password="x", is_staff=True, status=User.Status.ACTIVE)


def _paid(user, staff, amount="1000000"):
    invoice = topups.create_invoice(user, Decimal(amount))
    topups.confirm_payment(invoice, staff, Decimal(amount), date(2026, 10, 30), ACCOUNT, MFO)
    return invoice


def _wallet(user):
    return Wallet.objects.get(user=user)


class AvailabilityTests(TestCase):
    def test_closed_without_operator_requisites(self):
        user = _advertiser()
        self.assertIn("банковские реквизиты", topups.top_up_error(user))
        self.client.force_login(user)
        self.assertContains(self.client.get(reverse("web:wallet")), "data-top-up-closed")
        with self.assertRaises(TopUpError):
            topups.create_invoice(user, Decimal("100"))

    @override_settings(**OPERATOR)
    def test_needs_inn(self):
        self.assertIn("ИНН", topups.top_up_error(_advertiser("noinn@test.local", inn="")))

    @override_settings(**OPERATOR)
    def test_demo_can_walk_through_process(self):
        self.assertIsNone(topups.top_up_error(_advertiser("demo@test.local", inn="", is_demo=True)))

    @override_settings(**OPERATOR)
    def test_blogger_has_no_form(self):
        blogger = User.objects.create_user(email="b@test.local", password="x", role=User.Role.BLOGGER,
                                           status=User.Status.ACTIVE)
        self.client.force_login(blogger)
        self.assertNotContains(self.client.get(reverse("web:wallet")), "data-top-up")
        self.assertEqual(self.client.post(reverse("web:invoice_create"), {"amount": "100"}).status_code, 404)


@override_settings(**OPERATOR, INVOICE_VALID_DAYS=10)
class InvoiceTests(TestCase):
    def setUp(self):
        self.user = _advertiser()
        self.staff = _staff()
        self.client.force_login(self.user)

    def test_any_amount_with_spaces_leads_to_print(self):
        response = self.client.post(reverse("web:invoice_create"), {"amount": "30 000"})
        invoice = PaymentInvoice.objects.get()
        self.assertRedirects(response, reverse("web:invoice_print", args=[invoice.pk]))
        self.assertEqual(invoice.amount, Decimal("30000"))
        page = self.client.get(reverse("web:invoice_print", args=[invoice.pk]))
        self.assertContains(page, invoice.number)
        self.assertContains(page, "20208000000000000099")
        self.assertContains(page, "ООО «EASTESSE GROUP»")
        self.assertContains(page, "305123456")

    def test_requisites_are_a_snapshot(self):
        invoice = topups.create_invoice(self.user, Decimal("100"))
        with override_settings(OPERATOR_ACCOUNT="20208000000000000077"):
            self.assertContains(self.client.get(reverse("web:invoice_print", args=[invoice.pk])), "20208000000000000099")

    def test_other_advertiser_cannot_open(self):
        invoice = topups.create_invoice(self.user, Decimal("100"))
        self.client.force_login(_advertiser("other@test.local", inn="305123457"))
        self.assertEqual(self.client.get(reverse("web:invoice_print", args=[invoice.pk])).status_code, 404)

    def test_credit_uses_statement_amount(self):
        invoice = topups.create_invoice(self.user, Decimal("1000000"))
        topups.confirm_payment(invoice, self.staff, Decimal("995000"), date(2026, 10, 30), ACCOUNT, MFO)
        invoice.refresh_from_db()
        self.assertEqual((invoice.status, invoice.received_amount, invoice.payer_account),
                         (PaymentInvoice.Status.PAID, Decimal("995000"), ACCOUNT))
        self.assertEqual(_wallet(self.user).available_balance, Decimal("995000"))
        row = Transaction.objects.get(type=Transaction.Type.DEPOSIT)
        self.assertEqual(row.amount, Decimal("995000"))
        self.assertTrue(Notification.objects.filter(user=self.user, title="Баланс пополнен").exists())
        self.assertEqual(metrics.advertiser_deposited(self.user), Decimal("995000"))

    def test_second_credit_refused(self):
        invoice = _paid(self.user, self.staff)
        with self.assertRaises(TopUpError):
            topups.confirm_payment(invoice, self.staff, Decimal("1000000"), date(2026, 10, 30), ACCOUNT, MFO)
        self.assertEqual(_wallet(self.user).available_balance, Decimal("1000000"))

    def test_bad_payer_account_refused(self):
        invoice = topups.create_invoice(self.user, Decimal("100"))
        with self.assertRaises(TopUpError):
            topups.confirm_payment(invoice, self.staff, Decimal("100"), date(2026, 10, 30), "123", MFO)
        self.assertEqual(_wallet(self.user).available_balance, 0)

    def test_expiry_then_late_payment(self):
        invoice = topups.create_invoice(self.user, Decimal("100"))
        self.assertAlmostEqual(invoice.expires_at, timezone.now() + timedelta(days=10), delta=timedelta(minutes=1))
        self.assertEqual(topups.expire_overdue(), 0)
        PaymentInvoice.objects.filter(pk=invoice.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(topups.expire_overdue(), 1)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, PaymentInvoice.Status.EXPIRED)
        topups.confirm_payment(invoice, self.staff, Decimal("100"), date(2026, 10, 30), ACCOUNT, MFO)
        self.assertEqual(_wallet(self.user).available_balance, Decimal("100"))

    def test_staff_queue_and_confirm_by_web(self):
        invoice = topups.create_invoice(self.user, Decimal("500000"))
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("web:admin_invoices")), "data-invoice-row")
        self.client.post(reverse("web:admin_invoice_confirm", args=[invoice.pk]),
                         {"amount": "500 000", "received_on": "2026-10-30", "account": ACCOUNT, "mfo": MFO})
        self.assertEqual(_wallet(self.user).available_balance, Decimal("500000"))
        self.assertNotContains(self.client.get(reverse("web:admin_invoices")), "data-invoice-row")

    def test_queue_only_for_staff(self):
        self.assertNotEqual(self.client.get(reverse("web:admin_invoices")).status_code, 200)

    def test_unpaid_invoice_blocks_closure(self):
        topups.create_invoice(self.user, Decimal("100"))
        self.assertIn("есть неоплаченные счета", data_requests.closure_blockers(self.user))


@override_settings(**OPERATOR)
class RefundTests(TestCase):
    def setUp(self):
        self.user = _advertiser()
        self.staff = _staff()
        _paid(self.user, self.staff)

    def _request(self, amount="300000", account=ACCOUNT):
        return topups.request_refund(self.user, Decimal(amount), account, MFO)

    def test_not_before_first_payment(self):
        fresh = _advertiser("fresh@test.local", inn="305123457")
        Wallet.objects.filter(user=fresh).update(available_balance=Decimal("100"))
        with self.assertRaises(TopUpError):
            topups.request_refund(fresh, Decimal("50"), ACCOUNT, MFO)

    def test_amount_leaves_available_at_once(self):
        refund = self._request()
        wallet = _wallet(self.user)
        self.assertEqual((wallet.available_balance, wallet.on_withdrawal), (Decimal("700000"), Decimal("300000")))
        self.assertEqual(wallet.total_balance, Decimal("1000000"))
        self.assertFalse(refund.other_account)

    def test_only_free_money(self):
        Wallet.objects.filter(user=self.user).update(available_balance=Decimal("100"), reserved_balance=Decimal("999900"))
        with self.assertRaises(TopUpError):
            self._request("101")
        self.assertEqual(BalanceRefund.objects.count(), 0)

    def test_repeat_cannot_exceed_balance(self):
        self._request("700000")
        with self.assertRaises(TopUpError):
            self._request("700000")

    def test_refund_money_cannot_pay_offer(self):
        self._request("1000000")
        self.assertEqual(_wallet(self.user).available_balance, 0)

    def test_deadlines_in_working_days(self):
        refund = self._request()
        self.assertGreater(refund.due_at, refund.info_due_at)
        self.assertGreaterEqual(refund.due_at - refund.created_at, timedelta(days=5) - timedelta(minutes=1))
        self.assertLessEqual(refund.due_at - refund.created_at, timedelta(days=9))

    def test_other_account_marked(self):
        self.assertTrue(self._request(account=OTHER_ACCOUNT).other_account)

    def test_cancel_returns_money(self):
        refund = self._request()
        topups.cancel_refund(refund, self.user)
        refund.refresh_from_db()
        self.assertEqual(refund.status, BalanceRefund.Status.CANCELLED)
        wallet = _wallet(self.user)
        self.assertEqual((wallet.available_balance, wallet.on_withdrawal), (Decimal("1000000"), Decimal("0")))

    def test_cancel_impossible_after_bank(self):
        refund = self._request()
        topups.mark_refund_sent(refund, self.staff)
        with self.assertRaises(TopUpError):
            topups.cancel_refund(refund, self.user)

    def test_full_path_without_commission(self):
        refund = self._request()
        topups.mark_refund_sent(refund, self.staff)
        topups.complete_refund(refund, self.staff)
        refund.refresh_from_db()
        self.assertEqual(refund.status, BalanceRefund.Status.DONE)
        wallet = _wallet(self.user)
        self.assertEqual((wallet.available_balance, wallet.on_withdrawal), (Decimal("700000"), Decimal("0")))
        self.assertEqual(Transaction.objects.get(type=Transaction.Type.PAYOUT).amount, Decimal("-300000"))
        self.assertEqual(metrics.platform_revenue(), 0)

    def test_complete_needs_sent(self):
        with self.assertRaises(TopUpError):
            topups.complete_refund(self._request(), self.staff)

    def test_bank_return_goes_back_to_balance(self):
        refund = self._request()
        topups.mark_refund_sent(refund, self.staff)
        topups.reject_refund(refund, self.staff, "Банк вернул платёж")
        wallet = _wallet(self.user)
        self.assertEqual((wallet.available_balance, wallet.on_withdrawal), (Decimal("1000000"), Decimal("0")))
        self.assertTrue(Notification.objects.filter(user=self.user, body__contains="Банк вернул").exists())

    def test_reject_needs_reason(self):
        with self.assertRaises(TopUpError):
            topups.reject_refund(self._request(), self.staff, " ")

    def test_needs_details_restarts_term(self):
        refund = self._request()
        BalanceRefund.objects.filter(pk=refund.pk).update(due_at=timezone.now() - timedelta(days=1))
        topups.ask_for_details(refund, self.staff, "Нужна доверенность")
        topups.update_details(refund, self.user, OTHER_ACCOUNT, MFO)
        refund.refresh_from_db()
        self.assertEqual(refund.status, BalanceRefund.Status.REQUESTED)
        self.assertGreater(refund.due_at, timezone.now())
        self.assertTrue(refund.other_account)

    def test_details_only_by_owner(self):
        refund = self._request()
        topups.ask_for_details(refund, self.staff, "Счёт?")
        with self.assertRaises(TopUpError):
            topups.update_details(refund, _advertiser("x@test.local", inn="305123458"), ACCOUNT, MFO)

    def test_web_path(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("web:wallet"))
        self.assertContains(page, f'value="{ACCOUNT}"')
        self.client.post(reverse("web:refund_create"), {"refund_amount": "200 000", "account": ACCOUNT, "mfo": MFO})
        refund = BalanceRefund.objects.get()
        self.assertEqual(refund.amount, Decimal("200000"))
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("web:admin_refunds")), "data-refund-row")
        self.client.post(reverse("web:admin_refund_action", args=[refund.pk]), {"action": "sent"})
        self.client.post(reverse("web:admin_refund_action", args=[refund.pk]), {"action": "done"})
        refund.refresh_from_db()
        self.assertEqual(refund.status, BalanceRefund.Status.DONE)

    def test_foreign_refund_not_cancelled_by_web(self):
        refund = self._request()
        self.client.force_login(_advertiser("x@test.local", inn="305123458"))
        self.assertEqual(self.client.post(reverse("web:refund_cancel", args=[refund.pk])).status_code, 404)

    def test_money_on_refund_blocks_closure(self):
        Wallet.objects.filter(user=self.user).update(available_balance=0, on_withdrawal=Decimal("1"))
        self.assertIn("на балансе есть деньги", data_requests.closure_blockers(self.user))

    def test_refund_keeps_billing_invariant(self):
        before = _wallet(self.user).total_balance
        refund = self._request()
        self.assertEqual(_wallet(self.user).total_balance, before)
        BillingService.cancel_balance_refund(refund)
        self.assertEqual(_wallet(self.user).total_balance, before)
