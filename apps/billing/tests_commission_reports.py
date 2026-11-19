"""Р23 (#77): отчёт комиссионера рекламодателю за месяц (ПР 8.9)."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from apps.billing import commission_reports as reports
from apps.billing.models import CommissionReport, Transaction
from apps.billing.tests_deal_payouts import DealPayoutBase, approved_card
from apps.notifications.models import Notification

D = Decimal


@override_settings(CONTRACTOR_FEE_PERCENT="10")
class CommissionReportTest(DealPayoutBase):
    def _month_report(self):
        approved_card(self.blogger)
        deal = self._paid()
        # Все операции — в прошлом месяце, отчёт формируется в первый день следующего.
        first = timezone.localdate().replace(day=1)
        moment = timezone.make_aware(datetime.combine(first - timedelta(days=3), datetime.min.time()))
        Transaction.objects.update(created_at=moment)
        self.assertEqual(reports.generate_for_previous_month(first), 1)
        return deal, CommissionReport.objects.get(advertiser=self.adv)

    def test_report_rows_and_totals(self):
        deal, report = self._month_report()
        self.assertEqual(report.period, (timezone.localdate().replace(day=1) - timedelta(days=1)).replace(day=1))
        row = report.data["rows"][0]
        self.assertEqual(row["deal"], deal.pk)
        self.assertEqual((D(row["services"]), D(row["paid_out"]), D(row["contractor_fee"]), D(row["commission"])),
                         (D("100000"), D("90000"), D("10000"), D("10000")))
        self.assertEqual(D(row["reserved"]), D("110000"))
        self.assertEqual(D(report.data["reserved_end"]), D("0"))
        self.assertEqual(D(report.data["available_end"]), D("890000"))
        self.assertTrue(Notification.objects.filter(user=self.adv, type=Notification.Type.COMMISSION_REPORT).exists())
        # Повторный запуск в тот же месяц нового отчёта не создаёт.
        self.assertEqual(reports.generate_for_previous_month(timezone.localdate().replace(day=1)), 0)

    def test_objection_then_staff_answer(self):
        _, report = self._month_report()
        with self.assertRaisesMessage(reports.ReportError, "Опишите"):
            reports.object_to(report.pk, self.adv, "")
        reports.object_to(report.pk, self.adv, "Сделка не моя")
        report.refresh_from_db()
        self.assertEqual(report.status, CommissionReport.Status.OBJECTED)
        self.assertTrue(Notification.objects.filter(user=self.staff, title="Возражение по отчёту комиссионера").exists())
        with self.assertRaises(reports.ReportError):
            reports.resolve(report.pk, self.adv, "ok")
        reports.resolve(report.pk, self.staff, "Проверили: сделка по вашему Поручению")
        report.refresh_from_db()
        self.assertEqual(report.status, CommissionReport.Status.RESOLVED)

    def test_accepted_without_objection_after_ten_working_days(self):
        _, report = self._month_report()
        self.assertEqual(reports.accept_overdue(), 0)
        CommissionReport.objects.filter(pk=report.pk).update(objection_until=timezone.now() - timedelta(minutes=1))
        self.assertEqual(reports.accept_overdue(), 1)
        report.refresh_from_db()
        self.assertEqual(report.status, CommissionReport.Status.ACCEPTED)
        with self.assertRaisesMessage(reports.ReportError, "не принят"):
            reports.object_to(report.pk, self.adv, "Поздно")

    def test_pages_and_export(self):
        _, report = self._month_report()
        self.client.force_login(self.adv)
        self.assertContains(self.client.get(reverse("web:wallet")), "data-commission-reports-link")
        self.assertContains(self.client.get(reverse("web:commission_report_list")), "data-commission-reports")
        page = self.client.get(reverse("web:commission_report_detail", kwargs={"pk": report.pk}))
        self.assertContains(page, "data-report-object-form")
        self.assertContains(page, "90 000")
        xlsx = self.client.get(reverse("web:commission_report_detail", kwargs={"pk": report.pk}), {"export": "xlsx"})
        sheet = load_workbook(BytesIO(xlsx.content)).active
        self.assertEqual(sheet.cell(row=2, column=1).value, report.data["rows"][0]["deal"])
        self.client.post(reverse("web:commission_report_object", kwargs={"pk": report.pk}), {"text": "Не согласны"})
        # Чужой отчёт не открывается.
        self.client.force_login(self.blogger)
        self.assertEqual(self.client.get(reverse("web:commission_report_detail", kwargs={"pk": report.pk})).status_code, 404)
        # Сотрудник видит возражение в очереди и отвечает.
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("web:admin_commission_reports")), "data-admin-commission-reports")
        self.client.post(reverse("web:admin_commission_report_resolve", kwargs={"pk": report.pk}), {"comment": "Ок"})
        report.refresh_from_db()
        self.assertEqual(report.status, CommissionReport.Status.RESOLVED)

    def test_no_report_without_operations(self):
        self.assertEqual(reports.generate_for_previous_month(date(2026, 1, 1)), 0)
