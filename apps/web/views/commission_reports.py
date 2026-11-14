"""Отчёт комиссионера (ПР 8.9): рекламодатель смотрит отчёты за месяц, скачивает Excel и направляет возражение;
сотрудник видит все отчёты и отвечает на возражения. Логика — apps/billing/commission_reports.py."""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.billing import commission_reports as reports
from apps.billing.exports import commission_report_workbook, xlsx_response
from apps.billing.metrics import REPORT_COLUMNS
from apps.billing.models import CommissionReport
from apps.users.models import User

from .admin_panel import _staff_required


def _rows(report):
    def cells(source):
        return [Decimal(source.get(key, "0")) for key, _ in REPORT_COLUMNS]

    rows = [{**row, "cells": cells(row)} for row in report.data.get("rows", [])]
    return rows, cells(report.data.get("totals", {}))


@login_required
def commission_report_list(request):
    if request.user.role != User.Role.ADVERTISER:
        return redirect("web:wallet")
    items = CommissionReport.objects.filter(advertiser=request.user)
    return render(request, "billing/commission_reports.html", {
        "reports": [(r, reports.period_label(r.period)) for r in items],
    })


@login_required
def commission_report_detail(request, pk):
    if request.user.is_staff:
        report = get_object_or_404(CommissionReport, pk=pk)
    else:
        report = get_object_or_404(CommissionReport, pk=pk, advertiser=request.user)
    if request.GET.get("export") == "xlsx":
        return xlsx_response(commission_report_workbook(report), f"report_{report.period:%Y-%m}.xlsx")
    rows, totals = _rows(report)
    return render(request, "billing/commission_report_detail.html", {
        "report": report, "period": reports.period_label(report.period), "columns": [label for _, label in REPORT_COLUMNS],
        "rows": rows, "totals": totals, "summary": {k: Decimal(report.data.get(k, "0"))
                                                     for k in ("deposits", "available_end", "reserved_end")},
    })


@login_required
@require_POST
def commission_report_object(request, pk):
    try:
        reports.object_to(pk, request.user, request.POST.get("text", ""))
    except reports.ReportError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, "Возражение направлено Оператору.")
    return redirect("web:commission_report_detail", pk=pk)


@_staff_required
def admin_commission_reports(request):
    status = request.GET.get("status", CommissionReport.Status.OBJECTED)
    items = CommissionReport.objects.select_related("advertiser")
    if status != "all":
        items = items.filter(status=status)
    return render(request, "admin_panel/commission_reports.html", {
        "reports": [(r, reports.period_label(r.period)) for r in items[:200]],
        "status": status, "statuses": CommissionReport.Status.choices,
    })


@_staff_required
@require_POST
def admin_commission_report_resolve(request, pk):
    try:
        reports.resolve(pk, request.user, request.POST.get("comment", ""))
    except reports.ReportError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, "Ответ на возражение отправлен рекламодателю.")
    return redirect("web:admin_commission_reports")
