"""Комиссия платформы по тарифным уровням (#38).

Ставка зависит от месячного оборота рекламодателя — суммы вознаграждений исполнителей по сделкам, оплаченным через
платформу за календарный месяц, без комиссии. Ставка на весь месяц считается по обороту прошлого календарного месяца,
новый рекламодатель — первый уровень. Комиссия начисляется на вознаграждение исполнителя и оплачивается рекламодателем
сверх него; фиксируется при направлении оферты (резерв = вознаграждение + комиссия). Специальный тариф рекламодателя
(`SpecialTariff`) действует вместо уровней.
"""
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

# (верхняя граница оборота включительно, ставка %); None — без верхней границы.
TIERS = (
    (Decimal("120000000"), Decimal("16")),
    (Decimal("1200000000"), Decimal("13")),
    (None, Decimal("8")),
)


def _previous_month(today):
    first = today.replace(day=1)
    prev_last = first - timedelta(days=1)
    return prev_last.year, prev_last.month


def monthly_turnover(advertiser, year, month):
    """Оборот за календарный месяц: вознаграждения по сделкам, оплаченным исполнителю в этом месяце (без комиссии,
    частично оплаченные — оплаченной частью)."""
    from django.db.models import Sum
    from django.db.models.functions import Coalesce

    from apps.deals.models import Deal

    start = timezone.make_aware(datetime(year, month, 1))
    end = timezone.make_aware(datetime(year + (month == 12), month % 12 + 1, 1))
    total = (
        Deal.objects.filter(advertiser=advertiser, status=Deal.Status.COMPLETED,
                            last_distributed_at__gte=start, last_distributed_at__lt=end)
        .aggregate(total=Sum(Coalesce("paid_amount", "amount")))["total"]
    )
    return total or Decimal("0")


def tier_percent(turnover):
    for limit, percent in TIERS:
        if limit is None or turnover <= limit:
            return percent
    return TIERS[-1][1]


def commission_percent_for(advertiser, today=None):
    """Ставка комиссии рекламодателя на сегодня: специальный тариф или уровень по обороту прошлого месяца."""
    from .models import SpecialTariff

    today = today or timezone.localdate()
    special = SpecialTariff.objects.filter(advertiser=advertiser).first()
    if special and (special.valid_until is None or special.valid_until >= today):
        return special.percent
    year, month = _previous_month(today)
    return tier_percent(monthly_turnover(advertiser, year, month))


def commission_amount(amount, percent):
    return (Decimal(amount) * Decimal(percent) / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def tier_description(advertiser, today=None):
    """Подпись для интерфейса: ставка и почему она такая."""
    from .models import SpecialTariff

    today = today or timezone.localdate()
    special = SpecialTariff.objects.filter(advertiser=advertiser).first()
    if special and (special.valid_until is None or special.valid_until >= today):
        return f"{special.percent.normalize()}% — специальный тариф"
    year, month = _previous_month(today)
    turnover = monthly_turnover(advertiser, year, month)
    from .formatting import format_money

    return (f"{tier_percent(turnover).normalize()}% — по обороту за {date(year, month, 1):%m.%Y}: "
            f"{format_money(turnover)}")
