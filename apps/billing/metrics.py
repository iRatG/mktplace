"""
Финансовые агрегаты для дашбордов — единственное место, которое знает правило знаков.

BillingService пишет списания с минусом (RESERVE, PAYMENT, WITHDRAWAL), а зачисления —
с плюсом (DEPOSIT, RELEASE, EARNING, REFUND, TEST_CREDIT). Отдельной транзакции «комиссия»
нет: комиссия платформы — это разница между тем, что рекламодатель оплатил (|PAYMENT|), и
тем, что начислено блогеру (EARNING).

Суммы берутся по модулю каждой строки, поэтому результат не зависит от того, с каким знаком
лежит конкретная запись, и всегда неотрицателен.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import Abs, TruncDate
from django.utils import timezone

from .models import Transaction

DEBIT_TYPES = (
    Transaction.Type.RESERVE,
    Transaction.Type.PAYMENT,
    Transaction.Type.WITHDRAWAL,
)
CREDIT_TYPES = (
    Transaction.Type.DEPOSIT,
    Transaction.Type.RELEASE,
    Transaction.Type.EARNING,
    Transaction.Type.REFUND,
    Transaction.Type.TEST_CREDIT,
)


def _abs_total(qs):
    return qs.aggregate(total=Sum(Abs("amount")))["total"] or Decimal("0")


def _payments():
    return Transaction.objects.filter(type=Transaction.Type.PAYMENT)


def _earnings():
    return Transaction.objects.filter(type=Transaction.Type.EARNING)


def deal_turnover(since=None):
    """Сколько рекламодатели оплатили по сделкам (и CPA-конверсиям); since — с какого момента."""
    qs = _payments()
    if since is not None:
        qs = qs.filter(created_at__gte=since)
    return _abs_total(qs)


def platform_revenue():
    """Комиссия платформы: оплачено рекламодателями минус начислено блогерам."""
    return _abs_total(_payments()) - _abs_total(_earnings())


def advertiser_spent(user):
    return _abs_total(_payments().filter(wallet__user=user))


def advertiser_deposited(user):
    return _abs_total(
        Transaction.objects.filter(wallet__user=user, type=Transaction.Type.DEPOSIT)
    )


def blogger_earned(user):
    return _abs_total(_earnings().filter(wallet__user=user))


def _top(qs, limit):
    return list(
        qs.values("wallet__user__email")
        .annotate(total=Sum(Abs("amount")))
        .order_by("-total")[:limit]
    )


def top_advertisers(limit=5):
    """Рекламодатели с наибольшими тратами: [{"wallet__user__email", "total"}], по убыванию."""
    return _top(_payments(), limit)


def top_bloggers(limit=5):
    """Блогеры с наибольшим заработком: [{"wallet__user__email", "total"}], по убыванию."""
    return _top(_earnings(), limit)


# ── Выгрузка для бухгалтерии и реестр выплат (13.10.2026, #59, #60) ─────────────

TOTAL_TYPES = (
    ("deposits", Transaction.Type.DEPOSIT),
    ("test_credits", Transaction.Type.TEST_CREDIT),
    ("reserves", Transaction.Type.RESERVE),
    ("releases", Transaction.Type.RELEASE),
    ("payments", Transaction.Type.PAYMENT),
    ("earnings", Transaction.Type.EARNING),
    ("withdrawals", Transaction.Type.WITHDRAWAL),
    ("payouts", Transaction.Type.PAYOUT),
    ("refunds", Transaction.Type.REFUND),
    ("corrections", Transaction.Type.CORRECTION),
)


def deal_commission(deal):
    """Комиссия платформы по сделке: оплачено рекламодателем минус начислено исполнителю (по модулю)."""
    return _abs_total(_payments().filter(deal=deal)) - _abs_total(_earnings().filter(deal=deal))


def blogger_paid_out(user):
    """Сколько исполнителю уже перечислено (проведённые выплаты)."""
    return _abs_total(Transaction.objects.filter(wallet__user=user, type=Transaction.Type.PAYOUT))


def period_bounds(date_from, date_to):
    """Местные даты → полуинтервал [start, end) в aware datetime по текущему часовому поясу."""
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(date_from, time.min), tz)
    end = timezone.make_aware(datetime.combine(date_to + timedelta(days=1), time.min), tz)
    return start, end


def transactions_between(start, end):
    return (
        Transaction.objects.filter(created_at__gte=start, created_at__lt=end)
        .select_related("wallet__user", "deal", "offer").order_by("created_at", "pk")
    )


def _empty_totals():
    totals = {key: Decimal("0") for key, _ in TOTAL_TYPES}
    totals["commission"] = Decimal("0")
    totals["count"] = 0
    return totals


def _fill(totals, rows):
    by_type = {key: tx_type for key, tx_type in TOTAL_TYPES}
    for row in rows:
        for key, tx_type in by_type.items():
            if row["type"] == tx_type:
                totals[key] += row["total"] or Decimal("0")
        totals["count"] += row["n"]
    totals["commission"] = totals["payments"] - totals["earnings"]
    return totals


def period_totals(start, end):
    """Итоги за период по типам (суммы по модулю), комиссия = оплаты − начисления, число операций."""
    rows = (
        Transaction.objects.filter(created_at__gte=start, created_at__lt=end)
        .values("type").annotate(total=Sum(Abs("amount")), n=Count("id"))
    )
    return _fill(_empty_totals(), rows)


def daily_totals(start, end):
    """Итоги по дням периода (день — по местному времени): [{"day": date, ...как period_totals}], по возрастанию."""
    tz = timezone.get_current_timezone()
    rows = (
        Transaction.objects.filter(created_at__gte=start, created_at__lt=end)
        .annotate(day=TruncDate("created_at", tzinfo=tz))
        .values("day", "type").annotate(total=Sum(Abs("amount")), n=Count("id")).order_by("day")
    )
    days = {}
    for row in rows:
        days.setdefault(row["day"], []).append(row)
    result = []
    for day in sorted(days):
        totals = _fill(_empty_totals(), days[day])
        totals["day"] = day
        result.append(totals)
    return result
