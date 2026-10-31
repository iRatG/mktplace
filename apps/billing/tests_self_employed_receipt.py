"""Выплата самозанятому: вместо ЭСФ — чек, номер вносит исполнитель, подтверждает сотрудник."""
from decimal import Decimal

from django.urls import reverse

from apps.billing import esf
from apps.billing.exports import payouts_workbook
from apps.billing.models import EInvoice, WithdrawalRequest
from apps.billing.services import BillingService
from apps.billing.tests_deal_payouts import DealPayoutBase, approved_card
from apps.notifications.models import Notification
from apps.profiles.models import BloggerProfile
from apps.users.models import User


class SelfEmployedReceiptTest(DealPayoutBase):
    def setUp(self):
        super().setUp()
        BloggerProfile.objects.filter(user=self.blogger).update(category=BloggerProfile.Category.SELF_EMPLOYED,
                                                                 is_self_employed_confirmed=True)
        approved_card(self.blogger)
        self.deal = self._paid()
        self.payout = WithdrawalRequest.objects.get(deal=self.deal)

    def _reasons(self):
        return WithdrawalRequest.objects.get(pk=self.payout.pk).blocked_reasons

    def test_no_esf_but_receipt_expected(self):
        self.assertEqual(EInvoice.objects.get(deal=self.deal, kind="performer").status, EInvoice.Status.NOT_REQUIRED)
        self.assertIn("ждёт чек самозанятого", self._reasons())
        self.assertFalse(Notification.objects.filter(type="esf_required").exists())

    def test_blogger_told_what_to_do(self):
        note = Notification.objects.get(user=self.blogger, title="Выставьте чек самозанятого")
        self.assertIn("100 000", note.body)
        self.assertEqual(note.url, f"/deals/{self.deal.pk}/")

    def test_payout_refused_until_confirmed(self):
        with self.assertRaisesMessage(ValueError, "ждёт чек самозанятого"):
            BillingService.complete_withdrawal(self.payout)

    def test_blogger_number_alone_does_not_unblock(self):
        esf.submit_receipt(self.deal, self.blogger, " 123456 ")
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.self_employed_receipt, "123456")
        self.assertIn("ждёт чек самозанятого", self._reasons())

    def test_confirmed_receipt_unblocks_payout(self):
        esf.submit_receipt(self.deal, self.blogger, "123456")
        esf.confirm_receipt(self.deal, "123456")
        self.assertEqual(self._reasons(), [])
        paid = BillingService.complete_withdrawal(self.payout)
        self.assertEqual(paid.status, WithdrawalRequest.Status.COMPLETED)

    def test_confirm_needs_number(self):
        with self.assertRaises(ValueError):
            esf.confirm_receipt(self.deal, "  ")

    def test_confirmed_number_cannot_be_changed_by_blogger(self):
        esf.confirm_receipt(self.deal, "123456")
        with self.assertRaises(ValueError):
            esf.submit_receipt(self.deal, self.blogger, "999")

    def test_other_party_cannot_submit(self):
        with self.assertRaises(ValueError):
            esf.submit_receipt(self.deal, self.adv, "123456")

    def test_receipt_survives_reissue(self):
        esf.confirm_receipt(self.deal, "123456")
        BillingService.reject_withdrawal(self.payout, "Банк вернул перевод")
        again = BillingService.reissue_deal_payout(self.deal)
        self.assertEqual(again.blocked_reasons, [])

    def test_web_path(self):
        self.client.force_login(self.blogger)
        page = self.client.get(reverse("web:deal_detail", args=[self.deal.pk]))
        self.assertContains(page, "data-receipt-form")
        self.client.post(reverse("web:deal_receipt", args=[self.deal.pk]), {"number": "777"})
        self.client.force_login(self.staff)
        register = self.client.get(reverse("web:admin_withdrawals"))
        self.assertContains(register, 'value="777"')
        self.client.post(reverse("web:admin_withdrawal_receipt", args=[self.payout.pk]), {"number": "777"})
        self.assertEqual(self._reasons(), [])

    def test_stranger_gets_404(self):
        stranger = User.objects.create_user(email="s@test.local", password="x", role=User.Role.BLOGGER,
                                            status=User.Status.ACTIVE)
        self.client.force_login(stranger)
        self.assertEqual(self.client.post(reverse("web:deal_receipt", args=[self.deal.pk]), {"number": "1"}).status_code, 404)

    def test_blogger_cannot_confirm(self):
        self.client.force_login(self.blogger)
        self.client.post(reverse("web:admin_withdrawal_receipt", args=[self.payout.pk]), {"number": "1"})
        self.deal.refresh_from_db()
        self.assertIsNone(self.deal.receipt_confirmed_at)

    def test_receipt_in_export(self):
        esf.confirm_receipt(self.deal, "123456")
        from io import BytesIO

        from openpyxl import load_workbook

        sheet = load_workbook(BytesIO(payouts_workbook(WithdrawalRequest.objects.filter(pk=self.payout.pk)))).active
        self.assertEqual(sheet.cell(row=1, column=sheet.max_column).value, "Чек самозанятого")
        self.assertEqual(sheet.cell(row=2, column=sheet.max_column).value, "123456")


class OtherCategoriesTest(DealPayoutBase):
    def test_individual_and_ip_not_asked_for_receipt(self):
        approved_card(self.blogger)
        deal = self._paid()
        self.assertNotIn("ждёт чек самозанятого", WithdrawalRequest.objects.get(deal=deal).blocked_reasons)
        self.assertFalse(Notification.objects.filter(title="Выставьте чек самозанятого").exists())
        self.assertEqual(Decimal(deal.blogger_earning), Decimal("100000"))
