"""Фильтр `money`: сумма или счётчик с пробелами между разрядами, без нулевых копеек.

Сама логика — `apps.billing.formatting.format_money`, общая с Python-текстами (уведомления,
сообщения), чтобы суммы выглядели одинаково везде. Для сумм и счётчиков в шаблонах —
только `|money`, не `floatformat` (за этим следит тест `tests_display_consistency`).
"""
from django import template

from apps.billing.formatting import format_money

register = template.Library()


@register.filter
def money(value):
    """1500000 → «1 500 000», Decimal("150000.00") → «150 000», 99.5 → «99.50»."""
    return format_money(value)
