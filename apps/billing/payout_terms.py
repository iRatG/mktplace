"""Срок выплаты исполнителю — 5 рабочих дней после основания (ПР 8.2, редакция 06.11.2026).

Распоряжение в банк — не позднее 5 рабочих дней после наступления основания выплаты, получения нужных документов
(ЭСФ, чек самозанятого) и подтверждённых реквизитов. Пока выплата заблокирована (`blocked_reasons`), срок не идёт:
`ready_since` ставится, когда снята последняя причина, и сбрасывается, если появилась новая. О недостающем исполнитель
узнаёт сразу (ПР 8.2: не позднее 2 рабочих дней). Выплаты до правила (`deadline_from_basis=False`) — по-старому, до
PAYOUT_DAY следующего месяца.

`refresh` вызывается везде, где меняется причина блокировки: открытие выплаты по сделке (после ЭСФ), подтверждение
реквизитов, статус ЭСФ исполнителя, подтверждение чека.
"""
from django.utils import timezone

PAYOUT_WORKING_DAYS = 5

# Об этих причинах при открытии выплаты уже пишут отдельные уведомления (ЭСФ, чек) — не дублировать.
COVERED_AT_OPEN = ("ждёт ЭСФ исполнителя", "ждёт чек самозанятого")


def due_date(ready_since):
    """Последний день для распоряжения в банк: ready_since + 5 рабочих дней (по местному времени)."""
    from apps.campaigns.validation import working_days_after

    return working_days_after(timezone.localtime(ready_since), PAYOUT_WORKING_DAYS).date()


def refresh(payout, opening=False):
    """Пересчитать готовность выплаты по сделке: ready_since и уведомление о недостающем. Без сохранения — ничего."""
    from apps.notifications.service import NotificationService

    from .models import WithdrawalRequest

    if payout is None or not payout.deadline_from_basis or payout.status != WithdrawalRequest.Status.PENDING:
        return payout
    reasons = payout.blocked_reasons
    fields = []
    if reasons:
        if payout.ready_since:
            payout.ready_since = None
            fields.append("ready_since")
        key = "; ".join(reasons)
        if key != payout.missing_notified:
            to_tell = [r for r in reasons if not (opening and r in COVERED_AT_OPEN)]
            if to_tell:
                NotificationService.notify_payout_missing(payout, to_tell)
            payout.missing_notified = key
            fields.append("missing_notified")
    else:
        if not payout.ready_since:
            payout.ready_since = timezone.now()
            fields.append("ready_since")
        if payout.missing_notified:
            payout.missing_notified = ""
            fields.append("missing_notified")
    if fields:
        payout.save(update_fields=[*fields, "updated_at"])
    return payout


def refresh_for_deal(deal):
    """ЭСФ или чек по сделке изменились — пересчитать её выплату."""
    payout = deal.payouts.exclude(status="rejected").first() if deal is not None else None
    return refresh(payout)


def refresh_for_user(user):
    """Реквизиты исполнителя подтверждены — пересчитать все его ожидающие выплаты по сделкам."""
    from .models import WithdrawalRequest

    for payout in WithdrawalRequest.objects.filter(
        blogger=user, status=WithdrawalRequest.Status.PENDING, deadline_from_basis=True,
    ).select_related("deal"):
        refresh(payout)
