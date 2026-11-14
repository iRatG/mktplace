"""Р18 (#72): срок выплаты — 5 рабочих дней после основания, документов и подтверждённых реквизитов (ПР 8.2)."""
from datetime import datetime, timedelta
from decimal import Decimal

from django.utils import timezone

from apps.billing import esf
from apps.billing.models import EInvoice, WithdrawalRequest, payment_deadline
from apps.billing.payout_requisites import approve_payout_requisites, submit_payout_requisites
from apps.billing.payout_terms import due_date
from apps.billing.tests_deal_payouts import DealPayoutBase, approved_card
from apps.notifications.models import Notification
from apps.profiles.models import BloggerProfile
from apps.registration.models import IdentityVerification


class DueDateTest(DealPayoutBase):
    def test_five_working_days_skip_weekend(self):
        friday = timezone.make_aware(datetime(2026, 11, 13, 15, 0))
        self.assertEqual(due_date(friday).isoformat(), "2026-11-20")
        wednesday = timezone.make_aware(datetime(2026, 11, 18, 10, 0))
        self.assertEqual(due_date(wednesday).isoformat(), "2026-11-25")


class PayoutFromBasisTest(DealPayoutBase):
    def test_ready_payout_due_in_five_working_days(self):
        approved_card(self.blogger)
        deal = self._paid()
        payout = WithdrawalRequest.objects.get(deal=deal)
        self.assertTrue(payout.deadline_from_basis)
        self.assertIsNotNone(payout.ready_since)
        self.assertEqual(payout.pay_by, due_date(payout.ready_since))
        self.assertFalse(payout.is_late)
        self.assertFalse(Notification.objects.filter(type=Notification.Type.PAYOUT_DOCUMENTS_MISSING).exists())

    def test_missing_requisites_notified_and_no_deadline_until_approved(self):
        deal = self._paid()
        payout = WithdrawalRequest.objects.get(deal=deal)
        self.assertIsNone(payout.ready_since)
        self.assertIsNone(payout.pay_by)
        self.assertFalse(payout.is_late)
        note = Notification.objects.get(user=self.blogger, type=Notification.Type.PAYOUT_DOCUMENTS_MISSING)
        self.assertIn("нет подтверждённых реквизитов", note.body)

        IdentityVerification.objects.create(user=self.blogger, full_name="Test Bloggerov", phone="+998901112233",
                                            pinfl="12345678901234", status="verified", verified_at=timezone.now())
        application = submit_payout_requisites(self.blogger, card_number="8600123456789012",
                                               card_holder_name="Test Bloggerov")
        approve_payout_requisites(application.pk, self.staff)
        payout.refresh_from_db()
        self.assertIsNotNone(payout.ready_since)
        self.assertEqual(payout.pay_by, due_date(payout.ready_since))

    def test_esf_wait_then_confirm_then_problem_resets(self):
        BloggerProfile.objects.filter(user=self.blogger).update(category=BloggerProfile.Category.IP,
                                                                 is_ip_confirmed=True)
        approved_card(self.blogger)
        deal = self._paid()
        payout = WithdrawalRequest.objects.get(deal=deal)
        self.assertIsNone(payout.ready_since)
        # О ЭСФ при открытии пишет своё уведомление — общее «не хватает» не дублирует.
        self.assertFalse(Notification.objects.filter(type=Notification.Type.PAYOUT_DOCUMENTS_MISSING).exists())
        einvoice = EInvoice.objects.get(deal=deal, kind=EInvoice.Kind.PERFORMER)
        esf.set_status(einvoice.pk, self.staff, status=EInvoice.Status.CONFIRMED, number="ESF-1")
        payout.refresh_from_db()
        self.assertIsNotNone(payout.ready_since)
        esf.set_status(einvoice.pk, self.staff, status=EInvoice.Status.MISMATCH, note="Сумма не та")
        payout.refresh_from_db()
        self.assertIsNone(payout.ready_since)
        note = Notification.objects.get(user=self.blogger, type=Notification.Type.PAYOUT_DOCUMENTS_MISSING)
        self.assertIn("ЭСФ: расхождение", note.body)

    def test_self_employed_receipt_starts_deadline(self):
        BloggerProfile.objects.filter(user=self.blogger).update(category=BloggerProfile.Category.SELF_EMPLOYED,
                                                                 is_self_employed_confirmed=True)
        approved_card(self.blogger)
        deal = self._paid()
        payout = WithdrawalRequest.objects.get(deal=deal)
        self.assertIsNone(payout.ready_since)
        esf.confirm_receipt(deal, "200abc")
        payout.refresh_from_db()
        self.assertIsNotNone(payout.ready_since)

    def test_payout_before_rule_keeps_monthly_deadline(self):
        approved_card(self.blogger)
        deal = self._paid()
        payout = WithdrawalRequest.objects.get(deal=deal)
        WithdrawalRequest.objects.filter(pk=payout.pk).update(deadline_from_basis=False)
        payout.refresh_from_db()
        self.assertEqual(payout.pay_by, payment_deadline(payout.created_at))

    def test_late_after_five_working_days(self):
        approved_card(self.blogger)
        deal = self._paid()
        WithdrawalRequest.objects.filter(deal=deal).update(ready_since=timezone.now() - timedelta(days=10))
        payout = WithdrawalRequest.objects.get(deal=deal)
        self.assertTrue(payout.is_late)

    def test_expected_payout_date_in_wallet(self):
        deal = self._deal()
        deal.refresh_from_db()
        if deal.payout_due:
            self.assertEqual(deal.payout_pay_by, due_date(deal.payout_due))
        self.assertEqual(Decimal(deal.amount), Decimal("100000"))
