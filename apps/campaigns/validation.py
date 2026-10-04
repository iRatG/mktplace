"""Согласованность параметров кампании — общая проверка для веб-формы и API."""
from .models import Campaign


def campaign_param_errors(*, payment_type, fixed_price, budget,
                          start_date, end_date, deadline, max_bloggers):
    """Вернуть {поле: сообщение} для несогласованных параметров кампании.

    Принимает итоговые значения полей (после разбора формы/сериализатора).
    Пустые значения не сравниваются — их обязательность проверяется отдельно.
    """
    errors = {}

    if start_date and end_date and end_date < start_date:
        errors["end_date"] = "Дата окончания не может быть раньше даты начала."

    if deadline:
        if start_date and deadline < start_date:
            errors["deadline"] = "Дедлайн контента не может быть раньше начала кампании."
        elif end_date and deadline > end_date:
            errors["deadline"] = "Дедлайн контента не может быть позже окончания кампании."

    if payment_type == Campaign.PaymentType.FIXED and fixed_price and budget:
        if fixed_price > budget:
            errors["fixed_price"] = "Цена за размещение не может быть больше бюджета."
        elif max_bloggers and max_bloggers * fixed_price > budget:
            fits = int(budget // fixed_price)
            errors["max_bloggers"] = (
                f"Бюджета хватает на {fits} {_bloggers_word(fits)} "
                f"при цене за размещение {_spaced(fixed_price)}."
            )

    return errors


def _bloggers_word(n):
    if n % 10 == 1 and n % 100 != 11:
        return "блогера"
    return "блогеров"


def _spaced(value):
    return f"{int(value):,}".replace(",", " ")
