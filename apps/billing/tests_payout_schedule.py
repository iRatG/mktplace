"""Выплаты раз в месяц: срок до 5 числа следующего месяца, отбор по месяцу в реестре, дата для блогера."""
from datetime import date, datetime, timedelta
from io import BytesIO

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from apps.billing.exports import payouts_workbook
from apps.billing.models import WithdrawalRequest, payment_deadline
from apps.billing.tests_deal_payouts import DealPayoutBase, approved_card
from apps.deals.models import Deal


def _at(year, month, day, hour=12):
    return timezone.make_aware(datetime(year, month, day, hour))


class DeadlineTests(TestCase):
    def test_next_month_fifth(self):
        self.assertEqual(payment_deadline(_at(2026, 10, 1)), date(2026, 11, 5))
        self.assertEqual(payment_deadline(_at(2026, 10, 31, 23)), date(2026, 11, 5))

    def test_december_goes_to_january(self):
        self.assertEqual(payment_deadline(_at(2026, 12, 15)), date(2027, 1, 5))

    def test_month_boundary_in_local_time(self):
        utc_evening = datetime(2026, 10, 31, 20, 0, tzinfo=timezone.get_fixed_timezone(0))
        local = timezone.localtime(utc_evening)
        expected = date(2026, 12, 5) if local.month == 11 else date(2026, 11, 5)
        self.assertEqual(payment_deadline(utc_evening), expected)

    @override_settings(PAYOUT_DAY=10)
    def test_day_from_setting(self):
        self.assertEqual(payment_deadline(_at(2026, 10, 1)), date(2026, 11, 10))


class RegisterTests(DealPayoutBase):
    def setUp(self):
        super().setUp()
        approved_card(self.blogger)
        self.deal = self._paid()
        self.payout = WithdrawalRequest.objects.get(deal=self.deal)
        self.client.force_login(self.staff)

    def _created(self, moment):
        WithdrawalRequest.objects.filter(pk=self.payout.pk).update(created_at=moment)
        self.payout.refresh_from_db()

    def test_pay_by_and_late(self):
        self._created(timezone.now() - timedelta(days=70))
        self.assertLess(self.payout.pay_by, timezone.localdate())
        self.assertTrue(self.payout.is_late)
        self.assertContains(self.client.get(reverse("web:admin_withdrawals")), "data-pay-by")

    def test_not_late_in_time(self):
        self.assertFalse(self.payout.is_late)

    def test_completed_is_never_late(self):
        self._created(_at(2026, 1, 15))
        WithdrawalRequest.objects.filter(pk=self.payout.pk).update(status=WithdrawalRequest.Status.COMPLETED)
        self.payout.refresh_from_db()
        self.assertFalse(self.payout.is_late)

    def test_month_filter(self):
        self._created(_at(2026, 9, 30))
        url = reverse("web:admin_withdrawals")
        self.assertEqual(list(self.client.get(url, {"month": "2026-09"}).context["withdrawals"]), [self.payout])
        self.assertEqual(list(self.client.get(url, {"month": "2026-10"}).context["withdrawals"]), [])

    def test_bad_month_ignored(self):
        response = self.client.get(reverse("web:admin_withdrawals"), {"month": "мусор"})
        self.assertEqual(list(response.context["withdrawals"]), [self.payout])

    def test_early_payment_still_possible(self):
        self.assertEqual(self.payout.blocked_reasons, [])

    def test_export_has_deadline(self):
        sheet = load_workbook(BytesIO(payouts_workbook(WithdrawalRequest.objects.filter(pk=self.payout.pk)))).active
        self.assertEqual(sheet.cell(row=1, column=sheet.max_column).value, "Оплатить до")

    def test_blogger_sees_date_on_deal(self):
        self.client.force_login(self.blogger)
        response = self.client.get(reverse("web:deal_detail", args=[self.deal.pk]))
        self.assertContains(response, "data-pay-by")
        self.assertContains(response, f"{self.payout.pay_by:%d.%m.%Y}")


class ExpectedPayoutTests(DealPayoutBase):
    def test_wallet_shows_month_rule(self):
        deal = self._deal()
        Deal.objects.filter(pk=deal.pk).update(status=Deal.Status.CHECKING, publication_at=timezone.now())
        deal.refresh_from_db()
        self.assertIsNotNone(deal.payout_pay_by)
        self.client.force_login(self.blogger)
        response = self.client.get(reverse("web:wallet"))
        self.assertContains(response, "data-expected-payouts")
        self.assertContains(response, f"до {deal.payout_pay_by:%d.%m.%Y}")

    def test_no_date_before_publication(self):
        self.assertIsNone(self._deal().payout_pay_by)
