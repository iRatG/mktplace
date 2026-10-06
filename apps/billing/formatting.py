"""Единый вид денежных сумм и счётчиков: пробелы между разрядами, без нулевых копеек.

Используется и в шаблонах (фильтр `money`), и в Python-текстах (уведомления, сообщения,
ошибки форм) — чтобы сумма везде выглядела одинаково: «1 500 000», а не «1500000» или «1,500,000».
Встроенный `intcomma` не подходит: его разделитель зависит от локали и USE_THOUSAND_SEPARATOR.
"""
from decimal import Decimal, InvalidOperation


def format_money(value):
    """1500000 → «1 500 000», Decimal("150000.00") → «150 000», 99.5 → «99.50», None → ''."""
    if value is None or value == "":
        return ""
    try:
        amount = Decimal(str(value).replace(" ", "").replace("\u00a0", ""))
    except (InvalidOperation, ValueError):
        return value
    if amount == amount.to_integral_value():
        return f"{int(amount):,}".replace(",", " ")
    return f"{amount.quantize(Decimal('0.01')):,}".replace(",", " ")
