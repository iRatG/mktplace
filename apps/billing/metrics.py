"""
Финансовые агрегаты для дашбордов — единственное место, которое знает правило знаков.

BillingService пишет списания с минусом (RESERVE, PAYMENT, WITHDRAWAL), а зачисления —
с плюсом (DEPOSIT, RELEASE, EARNING, REFUND, TEST_CREDIT). Отдельной транзакции «комиссия»
нет: комиссия платформы — это разница между тем, что рекламодатель оплатил (|PAYMENT|), и
тем, что начислено блогеру (EARNING).

Суммы берутся по модулю каждой строки, поэтому результат не зависит от того, с каким знаком
лежит конкретная запись, и всегда неотрицателен.
"""
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import Abs

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
