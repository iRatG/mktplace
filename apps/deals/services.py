"""Переходы сделки — единственное место, где меняется статус сделки.

Сайт, API, таймеры Celery и панель сотрудника только вызывают эти функции. Каждый переход:
1. блокирует строку сделки (`select_for_update`) внутри `transaction.atomic`;
2. проверяет, кто действует и допустим ли переход из текущего статуса (иначе `TransitionError`);
3. пишет `DealStatusLog` ДО смены статуса;
4. выполняет деньги через `BillingService`, системное сообщение в чат;
5. создаёт уведомления после атомарного блока перехода.

`actor=None` — действие системы (таймер).
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.billing.services import BillingService
from apps.notifications.service import NotificationService

from .models import ChatMessage, Deal, DealStatusLog, PublicationDateChange

S = Deal.Status

CREATIVE_AUTO_APPROVE_AFTER = timedelta(hours=48)
CHECKING_AUTO_COMPLETE_AFTER = timedelta(hours=72)
WAITING_PAYMENT_AUTO_CANCEL_AFTER = timedelta(hours=24)

BLOGGER_CANCELLABLE = (S.WAITING_PAYMENT,)
ADVERTISER_CANCELLABLE = (S.WAITING_PAYMENT, S.IN_PROGRESS, S.WAITING_PUBLICATION)
DISPUTABLE = (S.CHECKING, S.PUBLISHED)


class TransitionError(Exception):
    """Переход невозможен — текст понятен пользователю."""


def _lock(pk):
    deal = Deal.objects.select_for_update().select_related("campaign", "advertiser", "blogger").filter(pk=pk).first()
    if deal is None:
        raise TransitionError("Сделка не найдена.")
    return deal


def _require(deal, statuses, message):
    if deal.status not in statuses:
        raise TransitionError(message)


def _require_actor(actor, user, message):
    if actor is not None and actor != user:
        raise TransitionError(message)


def _move(deal, new_status, actor, comment, **fields):
    """Журнал ДО смены статуса, затем статус и поля одним сохранением."""
    DealStatusLog.log(deal, new_status, changed_by=actor, comment=comment)
    for name, value in fields.items():
        setattr(deal, name, value)
    deal.status = new_status
    deal.save(update_fields=["status", "updated_at", *fields.keys()])
    if new_status not in Deal.UNPUBLISHED_STATUSES:
        # Публикация, отмена или спор — переносить дату больше нечего.
        PublicationDateChange.objects.filter(deal=deal, status=PublicationDateChange.Status.PENDING).update(
            status=PublicationDateChange.Status.CLOSED, answered_at=timezone.now(),
        )


def _chat(deal, text):
    ChatMessage.objects.create(deal=deal, text=text, is_system=True)


def _after_commit(func, *args, **kwargs):
    """Уведомление после атомарного блока перехода. Это строка в той же БД: если внешняя транзакция
    откатится, откатится и оно — отдельный on_commit не нужен."""
    func(*args, **kwargs)


# ── Создание ─────────────────────────────────────────────────────────────────

def create_deal(*, campaign, blogger, platform, advertiser, amount, actor, comment, response=None,
                offer=None, publication_date=None):
    """Сделка по акцепту индивидуальной оферты — сразу «В работе».

    Деньги зарезервированы при направлении оферты (``offer.reserved_at``) — повторного резерва нет, записи
    резерва привязываются к сделке. Оферта без резерва (направлена до этого правила) — резерв здесь.
    Вызывать внутри atomic после блокировки кампании. Нехватка денег — ValueError из BillingService.
    """
    from apps.billing.models import Transaction

    deal = Deal.objects.create(
        campaign=campaign, blogger=blogger, platform=platform, advertiser=advertiser,
        response=response, amount=amount, status=S.WAITING_PAYMENT, publication_date=publication_date,
    )
    if offer is not None and offer.reserved_at:
        Transaction.objects.filter(offer=offer, type=Transaction.Type.RESERVE).update(deal=deal)
    else:
        BillingService.reserve_funds(deal)
    _move(deal, S.IN_PROGRESS, actor, comment)
    return deal


# ── Креатив ──────────────────────────────────────────────────────────────────

def submit_creative(pk, actor, text, media=None):
    with transaction.atomic():
        deal = _lock(pk)
        _require_actor(actor, deal.blogger, "Отправить креатив может только блогер сделки.")
        if deal.creative_approved_at:
            raise TransitionError("Креатив уже согласован — можно публиковать.")
        _require(deal, (S.IN_PROGRESS,), "Отправить креатив можно только для сделки «В работе».")
        fields = {"creative_text": text, "creative_submitted_at": timezone.now(), "creative_rejection_reason": ""}
        if media:
            fields["creative_media"] = media
        _move(deal, S.ON_APPROVAL, actor, "Блогер отправил креатив на согласование.", **fields)
        _chat(deal, "Блогер отправил креатив на согласование.")
    _after_commit(NotificationService.notify_creative_submitted, deal.advertiser, deal)
    return deal


def approve_creative(pk, actor=None):
    """Рекламодатель согласовал креатив; actor=None — автоодобрение через 48 часов."""
    with transaction.atomic():
        deal = _lock(pk)
        _require_actor(actor, deal.advertiser, "Согласовать креатив может только рекламодатель сделки.")
        _require(deal, (S.ON_APPROVAL,), "Согласовать можно только сделку «На согласовании».")
        comment = "Рекламодатель согласовал креатив." if actor else "Креатив согласован автоматически: 48 часов без ответа."
        _move(deal, S.WAITING_PUBLICATION, actor, comment,
              creative_approved_at=timezone.now(), creative_rejection_reason="")
        _chat(deal, f"{comment} Можно публиковать!")
    _after_commit(NotificationService.notify_creative_approved, deal.blogger, deal)
    return deal


def reject_creative(pk, actor, reason):
    reason = (reason or "").strip()
    if not reason:
        raise TransitionError("Укажите причину отклонения.")
    with transaction.atomic():
        deal = _lock(pk)
        _require_actor(actor, deal.advertiser, "Отклонить креатив может только рекламодатель сделки.")
        _require(deal, (S.ON_APPROVAL,), "Отклонить можно только сделку «На согласовании».")
        _move(deal, S.IN_PROGRESS, actor, f"Рекламодатель отклонил креатив: {reason}",
              creative_rejection_reason=reason)
        _chat(deal, f"Рекламодатель отклонил креатив. Причина: {reason}")
    _after_commit(NotificationService.notify_creative_rejected, deal.blogger, deal)
    return deal


# ── Публикация и завершение ──────────────────────────────────────────────────

def submit_publication(pk, actor, url):
    url = (url or "").strip()
    if not url:
        raise TransitionError("Укажите ссылку на публикацию.")
    if not url.startswith(("http://", "https://")):
        raise TransitionError("Ссылка должна начинаться с http:// или https://")
    with transaction.atomic():
        deal = _lock(pk)
        _require_actor(actor, deal.blogger, "Добавить публикацию может только блогер сделки.")
        _require(deal, (S.IN_PROGRESS, S.WAITING_PUBLICATION),
                 "Добавить публикацию можно только для сделки «В работе» или «Ждёт публикации».")
        if deal.publication_date and timezone.localdate() < deal.publication_date:
            raise TransitionError(f"Публикация — не раньше {deal.publication_date:%d.%m.%Y}.")
        _move(deal, S.CHECKING, actor, f"Публикация размещена: {url}",
              publication_url=url, publication_at=timezone.now())
        _chat(deal, f"Блогер добавил публикацию: {url}")
    _after_commit(NotificationService.notify_publication_submitted, deal)
    return deal


def complete(pk, actor=None):
    """Рекламодатель подтвердил публикацию; actor=None — автозавершение через 72 часа после публикации."""
    with transaction.atomic():
        deal = _lock(pk)
        _require_actor(actor, deal.advertiser, "Подтвердить публикацию может только рекламодатель сделки.")
        _require(deal, (S.CHECKING,), "Подтвердить можно только сделку «На проверке».")
        if actor is None and not checking_overdue(deal):
            raise TransitionError("72 часа после публикации ещё не прошли.")
        comment = ("Рекламодатель подтвердил публикацию. Оплата выполнена." if actor
                   else "Публикация подтверждена автоматически: 72 часа без ответа рекламодателя. Оплата выполнена.")
        BillingService.complete_deal_payment(deal)
        _move(deal, S.COMPLETED, actor, comment, last_distributed_at=timezone.now())
        _chat(deal, comment)
    _after_commit(NotificationService.notify_deal_completed, deal.blogger, deal)
    return deal


def checking_overdue(deal, now=None):
    """72 часа проверки отсчитываются от публикации (а не от любого сохранения сделки)."""
    started = deal.publication_at or deal.updated_at
    return started <= (now or timezone.now()) - CHECKING_AUTO_COMPLETE_AFTER


# ── Спор ─────────────────────────────────────────────────────────────────────

def open_dispute(pk, actor, reason):
    reason = (reason or "").strip()
    if not reason:
        raise TransitionError("Опишите причину спора.")
    with transaction.atomic():
        deal = _lock(pk)
        if actor not in (deal.blogger, deal.advertiser):
            raise TransitionError("Открыть спор может только участник сделки.")
        _require(deal, DISPUTABLE, "Спор можно открыть только для сделки «На проверке».")
        _move(deal, S.DISPUTED, actor, f"Открыт спор: {reason}",
              dispute_reason=reason, dispute_opened_at=timezone.now(), is_frozen=True)
        _chat(deal, f"Открыт спор. Причина: {reason}. Деньги заморожены до решения сотрудника.")
    _after_commit(NotificationService.notify_dispute_opened, deal, actor)
    return deal


def resolve_dispute(pk, staff, resolution, comment=""):
    """Решение сотрудника: «complete» — оплата блогеру, «cancel» — возврат рекламодателю."""
    if staff is None or not staff.is_staff:
        raise TransitionError("Разрешить спор может только сотрудник.")
    if resolution not in ("complete", "cancel"):
        raise TransitionError("Укажите решение: оплатить блогеру или вернуть рекламодателю.")
    comment = (comment or "").strip()
    with transaction.atomic():
        deal = _lock(pk)
        _require(deal, (S.DISPUTED,), "Сделка уже не в статусе спора.")
        fields = {"dispute_resolved_at": timezone.now(), "dispute_resolution": comment}
        if resolution == "complete":
            BillingService.complete_deal_payment(deal)
            _move(deal, S.COMPLETED, staff,
                  f"Досудебное урегулирование: оплата переведена блогеру по итогам рассмотрения. {comment}".strip(),
                  last_distributed_at=timezone.now(), **fields)
        else:
            BillingService.release_funds(deal)
            _move(deal, S.CANCELLED, staff,
                  f"Досудебное урегулирование: средства возвращены рекламодателю по итогам рассмотрения. {comment}".strip(),
                  **fields)
        _chat(deal, "Спор разрешён сотрудником: " + ("оплата переведена блогеру." if resolution == "complete"
                                                     else "средства возвращены рекламодателю."))
    _after_commit(NotificationService.notify_dispute_resolved, deal)
    return deal


# ── Перенос даты публикации по согласию сторон ───────────────────────────────

def _other_side(deal, user):
    return deal.advertiser if user == deal.blogger else deal.blogger


def _date_error(deal, day):
    from apps.campaigns.validation import publication_date_error

    return publication_date_error(deal.campaign, day)


def propose_publication_date(pk, actor, new_date):
    """Сторона сделки предлагает новую дату публикации; вступает в силу после согласия второй стороны."""
    with transaction.atomic():
        deal = _lock(pk)
        if actor not in (deal.blogger, deal.advertiser):
            raise TransitionError("Перенести дату может только участник сделки.")
        _require(deal, Deal.UNPUBLISHED_STATUSES, "Перенести дату можно, пока публикации нет.")
        if not deal.publication_date:
            raise TransitionError("У сделки нет даты публикации.")
        if PublicationDateChange.objects.filter(deal=deal, status=PublicationDateChange.Status.PENDING).exists():
            raise TransitionError("Предыдущее предложение о переносе даты ещё ждёт ответа.")
        error = _date_error(deal, new_date)
        if error:
            raise TransitionError(error)
        if new_date == deal.publication_date:
            raise TransitionError("Новая дата совпадает с текущей.")
        change = PublicationDateChange.objects.create(
            deal=deal, proposed_by=actor, old_date=deal.publication_date, new_date=new_date,
        )
        side = "Блогер" if actor == deal.blogger else "Рекламодатель"
        _chat(deal, f"{side} предлагает перенести дату публикации с {change.old_date:%d.%m.%Y} на {new_date:%d.%m.%Y}.")
    _after_commit(NotificationService.notify_publication_date_proposed, _other_side(deal, actor), deal, change)
    return change


def _pending_change_for_answer(deal, actor):
    if actor not in (deal.blogger, deal.advertiser):
        raise TransitionError("Ответить на перенос даты может только участник сделки.")
    change = (
        PublicationDateChange.objects.select_for_update()
        .filter(deal=deal, status=PublicationDateChange.Status.PENDING).first()
    )
    if change is None:
        raise TransitionError("Нет предложения о переносе даты, ожидающего ответа.")
    if change.proposed_by_id == actor.pk:
        raise TransitionError("Ответить на предложение должна вторая сторона.")
    return change


def accept_publication_date(pk, actor):
    """Вторая сторона согласна: дата сделки меняется, напоминание и просрочка — заново для новой даты."""
    with transaction.atomic():
        deal = _lock(pk)
        change = _pending_change_for_answer(deal, actor)
        _require(deal, Deal.UNPUBLISHED_STATUSES, "Перенести дату можно, пока публикации нет.")
        error = _date_error(deal, change.new_date)
        if error:
            raise TransitionError(f"{error} Предложите другую дату.")
        comment = f"Дата публикации перенесена с {change.old_date:%d.%m.%Y} на {change.new_date:%d.%m.%Y} по согласию сторон."
        DealStatusLog.log(deal, deal.status, changed_by=actor, comment=comment)
        deal.publication_date = change.new_date
        deal.publication_reminder_sent_at = None
        deal.overdue_notified_at = None
        deal.save(update_fields=["publication_date", "publication_reminder_sent_at", "overdue_notified_at", "updated_at"])
        change.status = PublicationDateChange.Status.ACCEPTED
        change.answered_at = timezone.now()
        change.save(update_fields=["status", "answered_at"])
        _chat(deal, comment)
    _after_commit(NotificationService.notify_publication_date_answered, change.proposed_by, deal, change)
    return change


def decline_publication_date(pk, actor):
    """Вторая сторона не согласна: дата остаётся прежней."""
    with transaction.atomic():
        deal = _lock(pk)
        change = _pending_change_for_answer(deal, actor)
        change.status = PublicationDateChange.Status.DECLINED
        change.answered_at = timezone.now()
        change.save(update_fields=["status", "answered_at"])
        _chat(deal, f"Перенос даты публикации на {change.new_date:%d.%m.%Y} отклонён. "
                    f"Дата остаётся {deal.publication_date:%d.%m.%Y}.")
    _after_commit(NotificationService.notify_publication_date_answered, change.proposed_by, deal, change)
    return change


# ── Отмена ───────────────────────────────────────────────────────────────────

def cancel(pk, actor):
    """Блогер — только до начала работы; рекламодатель — пока публикации нет."""
    with transaction.atomic():
        deal = _lock(pk)
        if actor == deal.blogger:
            allowed = BLOGGER_CANCELLABLE
        elif actor == deal.advertiser:
            allowed = ADVERTISER_CANCELLABLE
        else:
            raise TransitionError("Отменить сделку может только её участник.")
        _require(deal, allowed, "Эту сделку нельзя отменить на текущем этапе.")
        BillingService.release_funds(deal)
        _move(deal, S.CANCELLED, actor, "Отменено блогером." if actor == deal.blogger else "Отменено рекламодателем.")
        _chat(deal, "Сделка отменена. Зарезервированные средства возвращены рекламодателю.")
    _after_commit(NotificationService.notify_deal_cancelled, deal, cancelled_by=actor)
    return deal


def auto_cancel_waiting_payment(pk):
    with transaction.atomic():
        deal = _lock(pk)
        _require(deal, (S.WAITING_PAYMENT,), "Сделка уже не ждёт оплаты.")
        if deal.created_at > timezone.now() - WAITING_PAYMENT_AUTO_CANCEL_AFTER:
            raise TransitionError("24 часа ещё не прошли.")
        BillingService.release_funds(deal)
        _move(deal, S.CANCELLED, None, "Отменено автоматически: 24 часа без оплаты.")
    return deal


# ── Таймеры ──────────────────────────────────────────────────────────────────

def _run_each(queryset, transition):
    count = 0
    for pk in list(queryset.values_list("pk", flat=True)):
        try:
            transition(pk)
            count += 1
        except TransitionError:
            pass  # сделку успели обработать другим путём
    return count


def auto_complete_overdue():
    threshold = timezone.now() - CHECKING_AUTO_COMPLETE_AFTER
    qs = Deal.objects.filter(status=S.CHECKING, publication_at__lte=threshold) | Deal.objects.filter(
        status=S.CHECKING, publication_at__isnull=True, updated_at__lte=threshold,
    )
    return _run_each(qs, lambda pk: complete(pk, actor=None))


def auto_approve_overdue_creatives():
    threshold = timezone.now() - CREATIVE_AUTO_APPROVE_AFTER
    qs = Deal.objects.filter(status=S.ON_APPROVAL, creative_submitted_at__lte=threshold)
    return _run_each(qs, lambda pk: approve_creative(pk, actor=None))


def auto_cancel_overdue_waiting_payment():
    threshold = timezone.now() - WAITING_PAYMENT_AUTO_CANCEL_AFTER
    qs = Deal.objects.filter(status=S.WAITING_PAYMENT, created_at__lte=threshold)
    return _run_each(qs, auto_cancel_waiting_payment)


def _flag_once(queryset, flag, now):
    """Отметить флагом и вернуть сделки, ещё не отмеченные (под блокировкой — без двойных уведомлений)."""
    with transaction.atomic():
        deals = list(queryset.filter(**{f"{flag}__isnull": True}).select_for_update(of=("self",))
                     .select_related("campaign", "advertiser", "blogger"))
        Deal.objects.filter(pk__in=[d.pk for d in deals]).update(**{flag: now})
    return deals


def send_publication_reminders(now=None):
    """Накануне даты публикации — одно напоминание исполнителю. Возвращает число напоминаний."""
    now = now or timezone.now()
    tomorrow = timezone.localdate(now) + timedelta(days=1)
    qs = Deal.objects.filter(status__in=Deal.UNPUBLISHED_STATUSES, publication_date=tomorrow)
    deals = _flag_once(qs, "publication_reminder_sent_at", now)
    for deal in deals:
        NotificationService.notify_publication_reminder(deal)
    return len(deals)


def notify_overdue_publications(now=None):
    """Дата публикации прошла, публикации нет — один раз уведомить обе стороны. Статус и деньги не меняются."""
    now = now or timezone.now()
    qs = Deal.objects.filter(status__in=Deal.OVERDUE_STATUSES, publication_date__lt=timezone.localdate(now))
    deals = _flag_once(qs, "overdue_notified_at", now)
    for deal in deals:
        NotificationService.notify_publication_overdue(deal)
    return len(deals)
