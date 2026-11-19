"""Комиссия рекламодателя по тарифу (таблица бизнеса 19.11.2026; до неё — уровни 16/13/8% по обороту, #38).

У каждого рекламодателя тариф (`TariffAssignment`, нет записи — тариф по умолчанию «Бесплатно»): ставка комиссии, лимит
кампаний в месяц и минимальный бюджет кампании. Комиссия начисляется на стоимость услуг исполнителя и оплачивается
рекламодателем сверх неё (ПР 7.1, 7.3); фиксируется при направлении оферты (ПР 7.4). Специальный тариф рекламодателя
(`SpecialTariff`, согласует директор) действует вместо ставки тарифа.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

# Если в базе нет ни одного тарифа (не должно быть: их создаёт миграция billing.0015) — ставка «Бесплатно».
FALLBACK = {"code": "free", "name": "Бесплатно", "commission_percent": Decimal("10"), "campaigns_per_month": 1,
            "min_campaign_budget": Decimal("1000000")}


def default_tariff():
    from .models import Tariff

    tariff = Tariff.objects.filter(is_default=True).first() or Tariff.objects.first()
    return tariff or Tariff(**FALLBACK)


def tariff_for(advertiser):
    from .models import TariffAssignment

    assignment = TariffAssignment.objects.select_related("tariff").filter(advertiser=advertiser).first()
    return assignment.tariff if assignment else default_tariff()


def _special(advertiser, today):
    from .models import SpecialTariff

    special = SpecialTariff.objects.filter(advertiser=advertiser).first()
    if special and (special.valid_until is None or special.valid_until >= today):
        return special
    return None


def commission_percent_for(advertiser, today=None):
    """Ставка комиссии рекламодателя на сегодня: специальный тариф или ставка его тарифа."""
    special = _special(advertiser, today or timezone.localdate())
    return special.percent if special else tariff_for(advertiser).commission_percent


def commission_amount(amount, percent):
    return (Decimal(amount) * Decimal(percent) / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def commission_description(advertiser, today=None):
    """Подпись для интерфейса: ставка и откуда она."""
    from .formatting import format_percent

    special = _special(advertiser, today or timezone.localdate())
    if special:
        return f"{format_percent(special.percent)}% — специальный тариф"
    tariff = tariff_for(advertiser)
    return f"{format_percent(tariff.commission_percent)}% — тариф «{tariff.name}»"


def assign_tariff(advertiser, tariff, staff):
    """Сотрудник назначает тариф рекламодателю. Сделки и направленные оферты не меняются (ПР 7.4)."""
    from .models import TariffAssignment

    TariffAssignment.objects.update_or_create(advertiser=advertiser, defaults={"tariff": tariff, "assigned_by": staff})


# ── Плата исполнителя за услуги платформы (ПР 7.1–7.6, редакция 06.11.2026) ──

def contractor_fee_percent_for(blogger, today=None):
    """Плата исполнителя на сегодня: индивидуальная (ContractorFeeRate) или CONTRACTOR_FEE_PERCENT по умолчанию."""
    from django.conf import settings

    from .models import ContractorFeeRate

    today = today or timezone.localdate()
    rate = ContractorFeeRate.objects.filter(blogger=blogger).first()
    if rate and (rate.valid_until is None or rate.valid_until >= today):
        return rate.percent
    return Decimal(str(getattr(settings, "CONTRACTOR_FEE_PERCENT", "10")))
