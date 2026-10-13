"""Страница сотрудника «Финансы»: итоги движений денег за день или период и выгрузка в Excel для бухгалтерии (#59).

Итоги считает apps/billing/metrics.py (суммы по модулю, день — по местному времени); файл собирает
apps/billing/exports.py. Выгрузка и просмотр пишутся в журнал доступа к персональным данным.
"""
from datetime import date

from django.shortcuts import render
from django.utils import timezone

from apps.billing import metrics
from apps.billing.exports import TOTAL_COLUMNS, finance_workbook, xlsx_response
from apps.users.pd_access import log_access

from .admin_panel import _staff_required


def parse_date(value):
    """«2026-10-13» → date, иначе None."""
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None


@_staff_required
def admin_finance(request):
    today = timezone.localdate()
    date_from = parse_date(request.GET.get("date_from")) or today
    date_to = parse_date(request.GET.get("date_to")) or date_from
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    start, end = metrics.period_bounds(date_from, date_to)
    period = f"{date_from:%d.%m.%Y}" if date_from == date_to else f"{date_from:%d.%m.%Y}–{date_to:%d.%m.%Y}"

    if request.GET.get("export") == "xlsx":
        log_access(request, f"Выгрузка движений денег за {period}")
        suffix = f"{date_from:%Y-%m-%d}" if date_from == date_to else f"{date_from:%Y-%m-%d}_{date_to:%Y-%m-%d}"
        return xlsx_response(finance_workbook(start, end), f"finance_{suffix}.xlsx")

    totals = metrics.period_totals(start, end)
    days = metrics.daily_totals(start, end)
    transactions = list(metrics.transactions_between(start, end)[:200])
    log_access(request, f"Финансы за {period}, операций: {totals['count']}")
    columns = [(key, label) for key, label in TOTAL_COLUMNS if key not in ("count", "tax")]
    return render(request, "admin_panel/finance.html", {
        "date_from": date_from,
        "date_to": date_to,
        "today": today,
        "period": period,
        "totals": totals,
        "total_rows": [(label, totals[key]) for key, label in columns],
        "columns": columns,
        "days": [(day["day"], [day[key] for key, _ in columns]) for day in days],
        "transactions": transactions,
        "shown": len(transactions),
    })
