"""Фильтр `money`: сумма с пробелами между разрядами, без нулевых копеек.

Встроенный `intcomma` не используется: его разделитель зависит от локали и
USE_THOUSAND_SEPARATOR, а суммы должны выглядеть одинаково везде.
"""
from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def money(value):
    """1500000 → «1 500 000», Decimal("150000.00") → «150 000», 99.5 → «99.50»."""
    if value is None or value == "":
        return ""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return value
    if amount == amount.to_integral_value():
        return f"{int(amount):,}".replace(",", " ")
    return f"{amount.quantize(Decimal('0.01')):,}".replace(",", " ")
