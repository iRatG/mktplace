"""Согласованность параметров кампании — общая проверка для веб-формы и API."""
from django.conf import settings

from apps.billing.formatting import format_money

from .models import Campaign

# Статусы, в которых владелец может редактировать кампанию; PAUSED после правки
# уходит в MODERATION (решение бизнеса 04.10.2026: «пауза → правка → модерация»).
EDITABLE_STATUSES = (Campaign.Status.DRAFT, Campaign.Status.REJECTED, Campaign.Status.PAUSED)

# Сделки, занимающие место в лимите блогеров кампании (max_bloggers): и при
# принятии отклика, и при правке лимита считаются одни и те же.
CAP_DEAL_STATUSES = (
    "in_progress", "on_approval", "waiting_publication", "checking", "completed",
)


def deals_in_cap(campaign):
    """Число сделок кампании, занимающих место в лимите блогеров."""
    from apps.deals.models import Deal

    if not campaign or not campaign.pk:
        return 0
    return Deal.objects.filter(campaign=campaign, status__in=CAP_DEAL_STATUSES).count()


# Окно приёма контента заканчивается не позже чем за столько рабочих дней до окончания кампании —
# чтобы рекламодатель успел проверить материалы (решение бизнеса 06.10.2026, рабочие дни — пн–пт).
CONTENT_END_WORKING_DAYS_BEFORE_END = 5


def working_days_before(day, count):
    """Дата, отстоящая от day на count рабочих дней назад (рабочие — пн–пт, праздники не учитываются)."""
    from datetime import timedelta

    current = day
    left = count
    while left > 0:
        current -= timedelta(days=1)
        if current.weekday() < 5:
            left -= 1
    return current


def latest_content_end(end_date):
    """Последний допустимый день окна приёма контента для кампании, заканчивающейся end_date."""
    return working_days_before(end_date, CONTENT_END_WORKING_DAYS_BEFORE_END)


def campaign_param_errors(*, payment_type, fixed_price, budget,
                          start_date, end_date, deadline, max_bloggers, taken_slots=0,
                          content_start=None):
    """Вернуть {поле: сообщение} для несогласованных параметров кампании.

    Принимает итоговые значения полей (после разбора формы/сериализатора).
    Пустые значения не сравниваются — их обязательность проверяется отдельно.
    """
    errors = {}

    if start_date and end_date and end_date < start_date:
        errors["end_date"] = "Дата окончания не может быть раньше даты начала."

    # Окно приёма контента: начало ≤ конец; конец не раньше старта кампании (правило до 06.10.2026 сохранено)
    # и не позже чем за N рабочих дней до её окончания.
    if content_start and deadline and content_start > deadline:
        errors["content_start"] = "Начало приёма контента не может быть позже его окончания."
    if deadline and start_date and deadline < start_date:
        errors["deadline"] = "Приём контента не может закончиться раньше начала кампании."
    elif deadline and end_date:
        latest = latest_content_end(end_date)
        if deadline > latest:
            errors["deadline"] = (
                f"Приём контента должен закончиться не позже чем за {CONTENT_END_WORKING_DAYS_BEFORE_END} "
                f"рабочих дней до окончания кампании — не позже {latest:%d.%m.%Y}."
            )

    min_price = settings.CAMPAIGN_MIN_FIXED_PRICE
    if payment_type == Campaign.PaymentType.FIXED and fixed_price and fixed_price < min_price:
        errors["fixed_price"] = f"Минимальная цена за размещение — {_spaced(min_price)}."
    elif payment_type == Campaign.PaymentType.FIXED and fixed_price and budget:
        if fixed_price > budget:
            errors["fixed_price"] = "Цена за размещение не может быть больше бюджета."
        elif max_bloggers and max_bloggers * fixed_price > budget:
            fits = int(budget // fixed_price)
            errors["max_bloggers"] = (
                f"Бюджета хватает на {fits} {_bloggers_word(fits)} "
                f"при цене за размещение {_spaced(fixed_price)}."
            )

    if max_bloggers and taken_slots and max_bloggers < taken_slots and "max_bloggers" not in errors:
        errors["max_bloggers"] = (
            f"По кампании уже занято мест: {taken_slots}. "
            f"Укажите не меньше {taken_slots} или 0 — без лимита."
        )

    return errors


def _bloggers_word(n):
    if n % 10 == 1 and n % 100 != 11:
        return "блогера"
    return "блогеров"


def active_response(campaign, blogger):
    """Ожидающий или принятый отклик блогера на кампанию — пока он есть, новый отклик не подать.

    Отклонённый и отозванный отклики не мешают: после отклонения блогер может откликнуться снова
    (решение бизнеса 06.10.2026). Одно правило для сайта и API.
    """
    from .models import Response

    return (
        Response.objects.filter(
            campaign=campaign, blogger=blogger,
            status__in=(Response.Status.PENDING, Response.Status.ACCEPTED),
        )
        .order_by("-created_at")
        .first()
    )


def _spaced(value):
    return format_money(value)
