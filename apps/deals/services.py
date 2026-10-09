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

from .models import (
    CLAIM_ANSWER_WORKING_DAYS, CLAIM_DECISION_WORKING_DAYS, CLAIM_EXTRA_DOCS_WORKING_DAYS, COMPENSATION_PERCENT,
    ChatMessage, Claim, ClaimFile, Deal, DealEvidence, DealStatusLog, PublicationDateChange, TerminationRequest,
)

S = Deal.Status

CREATIVE_AUTO_APPROVE_AFTER = timedelta(hours=48)
CHECKING_AUTO_COMPLETE_AFTER = timedelta(hours=72)
WAITING_PAYMENT_AUTO_CANCEL_AFTER = timedelta(hours=24)

BLOGGER_CANCELLABLE = (S.WAITING_PAYMENT,)
ADVERTISER_CANCELLABLE = (S.WAITING_PAYMENT, S.IN_PROGRESS, S.WAITING_PUBLICATION)


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
        # Публикация, отмена или претензия — переносить дату и прекращать по соглашению больше нечего.
        for model in (PublicationDateChange, TerminationRequest):
            model.objects.filter(deal=deal, status=model.Status.PENDING).update(
                status=model.Status.CLOSED, answered_at=timezone.now(),
            )


def _chat(deal, text):
    ChatMessage.objects.create(deal=deal, text=text, is_system=True)


def _after_commit(func, *args, **kwargs):
    """Уведомление после атомарного блока перехода. Это строка в той же БД: если внешняя транзакция
    откатится, откатится и оно — отдельный on_commit не нужен."""
    func(*args, **kwargs)


# ── Создание ─────────────────────────────────────────────────────────────────

def placement_terms(terms):
    """Срок сохранения и доказательства из снимка оферты. Нет в снимке — сделка по прежним правилам (72 часа)."""
    terms = terms or {}
    if "min_retention_days" not in terms:
        return {}
    evidence = terms.get("evidence_required") or []
    return {"min_retention_days": int(terms["min_retention_days"] or 3),
            "evidence_required": list(evidence) if isinstance(evidence, (list, tuple)) else []}


def approval_terms(terms):
    """Порядок согласования из снимка условий оферты. Оферта без этих условий (направлена до правила) —
    согласование не обязательно: сделка живёт по условиям на момент заключения."""
    terms = terms or {}
    if "approval_required" not in terms:
        return {"approval_required": False}
    return {
        "approval_required": str(terms["approval_required"]).strip().lower() in ("true", "on", "1"),
        "content_lead_days": int(terms.get("content_lead_days") or 5),
        "review_days": int(terms.get("review_days") or 2),
    }


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
        **approval_terms(offer.terms if offer is not None else None),
        **placement_terms(offer.terms if offer is not None else None),
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
        fields = {"creative_text": text, "creative_submitted_at": timezone.now(), "creative_rejection_reason": "",
                  "creative_submissions": deal.creative_submissions + 1, "review_overdue_notified_at": None}
        if media:
            fields["creative_media"] = media
        comment = "Блогер отправил креатив на согласование."
        if deal.creative_submissions:
            comment = "Блогер отправил исправленный креатив на согласование."
        due = deal.content_due
        if due and timezone.localdate() > due:
            comment += f" Срок сдачи по оферте — {due:%d.%m.%Y}, материал сдан позже."
        _move(deal, S.ON_APPROVAL, actor, comment, **fields)
        _chat(deal, comment)
    _after_commit(NotificationService.notify_creative_submitted, deal.advertiser, deal)
    return deal


def approve_creative(pk, actor=None):
    """Рекламодатель согласовал креатив; actor=None — автоодобрение через 48 часов, только если согласование
    по оферте не обязательно (при обязательном согласовании молчание рекламодателя согласием не считается)."""
    with transaction.atomic():
        deal = _lock(pk)
        _require_actor(actor, deal.advertiser, "Согласовать креатив может только рекламодатель сделки.")
        _require(deal, (S.ON_APPROVAL,), "Согласовать можно только сделку «На согласовании».")
        if actor is None and deal.approval_required:
            raise TransitionError("По условиям сделки материал согласует только рекламодатель.")
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

def _evidence_missing(deal, evidence):
    from apps.campaigns.models import EVIDENCE_CHOICES

    labels = dict(EVIDENCE_CHOICES)
    return [labels.get(kind, kind) for kind in deal.evidence_required if not (evidence or {}).get(kind)]


def submit_publication(pk, actor, url, evidence=None):
    """Блогер размещает публикацию: ссылка и доказательства, которые требует оферта (evidence — {вид: [файлы]})."""
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
        if deal.approval_required and not deal.creative_approved_at:
            raise TransitionError("По условиям сделки публиковать можно только после согласования материала рекламодателем.")
        if deal.publication_date and timezone.localdate() < deal.publication_date:
            raise TransitionError(f"Публикация — не раньше {deal.publication_date:%d.%m.%Y}.")
        missing = _evidence_missing(deal, evidence)
        if missing:
            raise TransitionError("Приложите доказательства исполнения: " + ", ".join(missing) + ".")
        for kind, files in (evidence or {}).items():
            if kind in deal.evidence_required:
                for f in files:
                    DealEvidence.objects.create(deal=deal, kind=kind, file=f, uploaded_by=actor)
        _move(deal, S.CHECKING, actor, f"Публикация размещена: {url}",
              publication_url=url, publication_at=timezone.now())
        _chat(deal, f"Блогер добавил публикацию: {url}")
    _after_commit(NotificationService.notify_publication_submitted, deal)
    return deal


def confirm_publication(pk, actor):
    """Рекламодатель принимает публикацию.

    Сделка по условиям оферты со сроком сохранения: отметка «принята» без оплаты — деньги уходят по окончании срока
    сохранения (претензия об удалении возможна весь срок). Сделка до правила: оплата сразу, как раньше.
    """
    with transaction.atomic():
        deal = _lock(pk)
        _require_actor(actor, deal.advertiser, "Принять публикацию может только рекламодатель сделки.")
        _require(deal, (S.CHECKING,), "Принять можно только сделку «На проверке».")
        if deal.min_retention_days is not None:
            if deal.publication_accepted_at:
                raise TransitionError("Публикация уже принята.")
            deal.publication_accepted_at = timezone.now()
            deal.save(update_fields=["publication_accepted_at", "updated_at"])
            comment = (f"Рекламодатель принял публикацию. Оплата исполнителю — "
                       f"{timezone.localtime(deal.payout_due):%d.%m.%Y %H:%M}, по окончании срока сохранения.")
            DealStatusLog.log(deal, deal.status, changed_by=actor, comment=comment)
            _chat(deal, comment)
    if deal.min_retention_days is None:
        return complete(pk, actor)
    _after_commit(NotificationService.notify_publication_accepted, deal)
    if deal.payout_due <= timezone.now():
        return complete(pk, actor=None)
    return deal


def complete(pk, actor=None):
    """Оплата исполнителю и завершение. actor=None — по сроку (таймер); рекламодатель — только у сделок до правила
    (подтверждение = оплата). Новые сделки рекламодатель принимает через confirm_publication."""
    with transaction.atomic():
        deal = _lock(pk)
        _require_actor(actor, deal.advertiser, "Подтвердить публикацию может только рекламодатель сделки.")
        _require(deal, (S.CHECKING,), "Подтвердить можно только сделку «На проверке».")
        if actor is not None and deal.min_retention_days is not None:
            raise TransitionError("Оплата по этой сделке — по окончании срока сохранения публикации.")
        if actor is None and not checking_overdue(deal):
            raise TransitionError("Срок для оплаты ещё не наступил.")
        if actor is not None:
            how, comment = "confirmed", "Рекламодатель подтвердил публикацию. Оплата выполнена."
        elif deal.min_retention_days is None:
            how = "timer"
            comment = "Публикация подтверждена автоматически: 72 часа без ответа рекламодателя. Оплата выполнена."
        elif deal.publication_accepted_at:
            how, comment = "accepted", "Публикация принята, срок сохранения истёк. Оплата выполнена."
        else:
            how = "timer"
            comment = "Претензий не поступило, срок претензии и срок сохранения истекли. Оплата выполнена."
        BillingService.complete_deal_payment(deal)
        _move(deal, S.COMPLETED, actor, comment, last_distributed_at=timezone.now())
        _chat(deal, comment)
    _after_commit(NotificationService.notify_deal_completed, deal.blogger, deal, how=how)
    return deal


def checking_overdue(deal, now=None):
    """Наступил ли срок оплаты (Deal.payout_due): по условиям оферты — поздний из сроков претензии и сохранения,
    у сделок до правила — 72 часа после публикации."""
    due = deal.payout_due or (deal.updated_at + CHECKING_AUTO_COMPLETE_AFTER)
    return due <= (now or timezone.now())


# ── Претензия ────────────────────────────────────────────────────────────────

CLAIMABLE = (S.IN_PROGRESS, S.ON_APPROVAL, S.WAITING_PUBLICATION, S.CHECKING)


def _claim_window_error(deal, actor, subject):
    """Срок претензии рекламодателя к размещению — 3 рабочих дня с загрузки подтверждения (или до принятия
    публикации); претензия об удалении публикации — весь срок сохранения, пока деньги не перечислены."""
    if actor != deal.advertiser or deal.status != S.CHECKING or deal.min_retention_days is None:
        return None
    if subject == Claim.Subject.RETENTION:
        return None
    if deal.publication_accepted_at:
        return ("Публикация уже принята — претензию можно подать только об удалении или скрытии публикации "
                f"(до {timezone.localtime(deal.retention_until):%d.%m.%Y %H:%M}).")
    if deal.claim_until and timezone.now() > deal.claim_until:
        return (f"Срок претензии по размещению истёк {timezone.localtime(deal.claim_until):%d.%m.%Y %H:%M}. "
                f"Претензию об удалении публикации можно подать до "
                f"{timezone.localtime(deal.retention_until):%d.%m.%Y %H:%M}.")
    return None


def open_claim(pk, actor, *, subject, violated_term, description, demand, demand_details="", links="", files=()):
    """Сторона сделки подаёт претензию: основание, нарушенное условие, требование и доказательства обязательны.
    Деньги остаются депонированными до решения сотрудника."""
    violated_term = (violated_term or "").strip()
    description = (description or "").strip()
    links = (links or "").strip()
    if subject not in Claim.Subject.values:
        raise TransitionError("Выберите, на что претензия.")
    if demand not in Claim.Demand.values:
        raise TransitionError("Выберите требование.")
    if not violated_term:
        raise TransitionError("Укажите, какое условие оферты или правило нарушено.")
    if not description:
        raise TransitionError("Опишите нарушение.")
    if not files and not links:
        raise TransitionError("Приложите доказательства: файлы (скриншоты, статистика, переписка) или ссылки.")
    from apps.campaigns.validation import working_days_after

    with transaction.atomic():
        deal = _lock(pk)
        if actor not in (deal.blogger, deal.advertiser):
            raise TransitionError("Подать претензию может только сторона сделки.")
        _require(deal, CLAIMABLE, "Претензию можно подать, пока сделка не завершена и деньги не перечислены.")
        error = _claim_window_error(deal, actor, subject)
        if error:
            raise TransitionError(error)
        now = timezone.now()
        claim = Claim.objects.create(
            deal=deal, author=actor, subject=subject, violated_term=violated_term, description=description,
            demand=demand, demand_details=(demand_details or "").strip(), links=links,
            answer_until=working_days_after(now, CLAIM_ANSWER_WORKING_DAYS),
            decide_until=working_days_after(now, CLAIM_DECISION_WORKING_DAYS),
        )
        for f in files:
            ClaimFile.objects.create(claim=claim, author=actor, file=f)
        side = "Рекламодатель" if actor == deal.advertiser else "Блогер"
        _move(deal, S.DISPUTED, actor, f"{side} подал претензию: {claim.get_subject_display()}. {description}",
              dispute_reason=description, dispute_opened_at=now, is_frozen=True)
        _chat(deal, f"{side} подал претензию: {claim.get_subject_display()}. Деньги остаются депонированными до "
                    f"решения сотрудника (до {timezone.localtime(claim.decide_until):%d.%m.%Y}).")
    _after_commit(NotificationService.notify_claim_opened, claim)
    return claim


def add_claim_materials(pk, actor, text="", files=()):
    """Объяснения второй стороны и дополнительные материалы любой стороны, пока претензия рассматривается."""
    text = (text or "").strip()
    if not text and not files:
        raise TransitionError("Добавьте объяснение или файлы.")
    with transaction.atomic():
        deal = _lock(pk)
        if actor not in (deal.blogger, deal.advertiser):
            raise TransitionError("Материалы к претензии добавляет только сторона сделки.")
        claim = Claim.objects.select_for_update().filter(
            deal=deal, status__in=[Claim.Status.OPEN, Claim.Status.EXTRA_DOCS]).first()
        if claim is None:
            raise TransitionError("Нет претензии, которая рассматривается.")
        if text:
            if actor == claim.respondent:
                claim.explanation = f"{claim.explanation}\n\n{text}".strip() if claim.explanation else text
                claim.explained_at = claim.explained_at or timezone.now()
            else:
                claim.demand_details = f"{claim.demand_details}\n\n{text}".strip() if claim.demand_details else text
            claim.save(update_fields=["explanation", "explained_at", "demand_details"])
        for f in files:
            ClaimFile.objects.create(claim=claim, author=actor, file=f)
        side = "Рекламодатель" if actor == deal.advertiser else "Блогер"
        _chat(deal, f"{side} добавил материалы к претензии.")
    _after_commit(NotificationService.notify_claim_materials, claim, actor)
    return claim


def resolve_claim(pk, staff, decision, comment, blogger_part=None):
    """Решение сотрудника по претензии: всё исполнителю, всё рекламодателю, раздел суммы, компенсация 30% или
    дополнительное документирование (до 7 рабочих дней, деньги остаются депонированными)."""
    from decimal import Decimal, InvalidOperation

    from apps.campaigns.validation import working_days_after

    if staff is None or not staff.is_staff:
        raise TransitionError("Решение по претензии принимает только сотрудник.")
    comment = (comment or "").strip()
    if not comment:
        raise TransitionError("Обоснуйте решение.")
    decisions = set(Claim.Decision.values) | {"extra_docs"}
    if decision not in decisions:
        raise TransitionError("Выберите решение.")
    with transaction.atomic():
        deal = _lock(pk)
        _require(deal, (S.DISPUTED,), "Сделка уже не в статусе претензии.")
        claim = Claim.objects.select_for_update().filter(
            deal=deal, status__in=[Claim.Status.OPEN, Claim.Status.EXTRA_DOCS]).first()
        now = timezone.now()
        if decision == "extra_docs":
            if claim is None:
                raise TransitionError("Дополнительное документирование — только по претензии.")
            if claim.status == Claim.Status.EXTRA_DOCS:
                raise TransitionError("Дополнительное документирование уже назначено.")
            claim.status = Claim.Status.EXTRA_DOCS
            claim.extra_docs_until = working_days_after(now, CLAIM_EXTRA_DOCS_WORKING_DAYS)
            claim.decision_comment = comment
            claim.save(update_fields=["status", "extra_docs_until", "decision_comment"])
            text = (f"Сотрудник запросил дополнительные документы до "
                    f"{timezone.localtime(claim.extra_docs_until):%d.%m.%Y}: {comment}")
            DealStatusLog.log(deal, deal.status, changed_by=staff, comment=text)
            _chat(deal, text)
            _after_commit(NotificationService.notify_claim_extra_docs, claim)
            return claim

        amount = deal.amount
        part = None
        if decision == Claim.Decision.SPLIT:
            try:
                part = Decimal(str(blogger_part).replace(" ", "").replace("\u00a0", ""))
            except (InvalidOperation, TypeError):
                raise TransitionError("Укажите сумму исполнителю.")
            if not (Decimal("0") < part < amount):
                raise TransitionError("Сумма исполнителю — больше нуля и меньше суммы сделки.")
        elif decision == Claim.Decision.COMPENSATION:
            part = (amount * COMPENSATION_PERCENT / Decimal("100")).quantize(Decimal("0.01"))

        fields = {"dispute_resolved_at": now, "dispute_resolution": comment}
        label = Claim.Decision(decision).label
        if decision == Claim.Decision.TO_BLOGGER:
            BillingService.complete_deal_payment(deal)
            _move(deal, S.COMPLETED, staff, f"Решение по претензии: {label}. {comment}",
                  last_distributed_at=now, paid_amount=amount, **fields)
        elif decision == Claim.Decision.TO_ADVERTISER:
            BillingService.release_funds(deal)
            _move(deal, S.CANCELLED, staff, f"Решение по претензии: {label}. {comment}", paid_amount=Decimal("0"), **fields)
        else:
            BillingService.split_deal_payment(deal, part)
            _move(deal, S.COMPLETED, staff, f"Решение по претензии: {label} — исполнителю {part}. {comment}",
                  last_distributed_at=now, paid_amount=part, **fields)
        if claim is not None:
            claim.status = Claim.Status.RESOLVED
            claim.decision = decision
            claim.blogger_part = part
            claim.decision_comment = comment
            claim.decided_by = staff
            claim.decided_at = now
            claim.save(update_fields=["status", "decision", "blogger_part", "decision_comment", "decided_by",
                                      "decided_at"])
        _chat(deal, f"Решение по претензии: {label}.")
    _after_commit(NotificationService.notify_dispute_resolved, deal)
    return deal


def remind_claim_decisions(now=None):
    """За рабочий день до срока решения — одно напоминание сотрудникам; истёк срок доп. документов — снова к решению."""
    from apps.campaigns.validation import working_days_after

    now = now or timezone.now()
    reminded = 0
    with transaction.atomic():
        claims = list(Claim.objects.select_for_update().filter(
            status=Claim.Status.OPEN, decision_reminder_sent_at__isnull=True).select_related("deal"))
        due = [c for c in claims if working_days_after(now, 1) >= c.decide_until]
        Claim.objects.filter(pk__in=[c.pk for c in due]).update(decision_reminder_sent_at=now)
        back = list(Claim.objects.select_for_update().filter(
            status=Claim.Status.EXTRA_DOCS, extra_docs_until__lte=now).select_related("deal"))
        Claim.objects.filter(pk__in=[c.pk for c in back]).update(status=Claim.Status.OPEN, decide_until=now,
                                                                decision_reminder_sent_at=None)
    for claim in due:
        NotificationService.notify_claim_decision_due(claim)
        reminded += 1
    for claim in back:
        claim.refresh_from_db()
        NotificationService.notify_claim_decision_due(claim)
    return reminded + len(back)


# ── Прекращение сделки по соглашению сторон ──────────────────────────────────

def propose_termination(pk, actor, reason):
    """Сторона предлагает прекратить заключённую сделку до публикации; причина обязательна."""
    reason = (reason or "").strip()
    if not reason:
        raise TransitionError("Укажите причину.")
    with transaction.atomic():
        deal = _lock(pk)
        if actor not in (deal.blogger, deal.advertiser):
            raise TransitionError("Предложить прекращение может только сторона сделки.")
        _require(deal, Deal.UNPUBLISHED_STATUSES,
                 "Прекратить по соглашению можно, пока публикации нет. После публикации — через претензию.")
        if TerminationRequest.objects.filter(deal=deal, status=TerminationRequest.Status.PENDING).exists():
            raise TransitionError("Предыдущее предложение о прекращении ещё ждёт ответа.")
        request = TerminationRequest.objects.create(deal=deal, proposed_by=actor, reason=reason)
        side = "Блогер" if actor == deal.blogger else "Рекламодатель"
        _chat(deal, f"{side} предлагает прекратить сделку по соглашению сторон. Причина: {reason}")
    _after_commit(NotificationService.notify_termination_proposed, request)
    return request


def _pending_termination(deal, actor):
    if actor not in (deal.blogger, deal.advertiser):
        raise TransitionError("Ответить может только сторона сделки.")
    request = (TerminationRequest.objects.select_for_update()
               .filter(deal=deal, status=TerminationRequest.Status.PENDING).first())
    if request is None:
        raise TransitionError("Нет предложения о прекращении, ожидающего ответа.")
    if request.proposed_by_id == actor.pk:
        raise TransitionError("Ответить на предложение должна вторая сторона.")
    return request


def accept_termination(pk, actor):
    """Вторая сторона согласна: сделка прекращена, рекламодателю — возврат за вычетом комиссии платформы."""
    with transaction.atomic():
        deal = _lock(pk)
        request = _pending_termination(deal, actor)
        _require(deal, Deal.UNPUBLISHED_STATUSES, "Прекратить по соглашению можно, пока публикации нет.")
        _, commission = BillingService.refund_minus_commission(deal)
        request.status = TerminationRequest.Status.ACCEPTED
        request.answered_at = timezone.now()
        request.save(update_fields=["status", "answered_at"])
        comment = (f"Сделка прекращена по соглашению сторон. Причина: {request.reason}. Рекламодателю возвращено "
                   f"{deal.amount - commission}, комиссия платформы {commission}.")
        _move(deal, S.CANCELLED, actor, comment, paid_amount=0)
        _chat(deal, "Сделка прекращена по соглашению сторон. Резерв возвращён рекламодателю за вычетом комиссии платформы.")
    _after_commit(NotificationService.notify_termination_answered, request)
    return request


def decline_termination(pk, actor):
    """Вторая сторона не согласна: сделка продолжается; при нарушениях — претензия."""
    with transaction.atomic():
        deal = _lock(pk)
        request = _pending_termination(deal, actor)
        request.status = TerminationRequest.Status.DECLINED
        request.answered_at = timezone.now()
        request.save(update_fields=["status", "answered_at"])
        _chat(deal, "Предложение прекратить сделку отклонено — сделка продолжается.")
    _after_commit(NotificationService.notify_termination_answered, request)
    return request


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
    """Односторонняя отмена — только у сделок, заключённых по прежним правилам (блогер — до начала работы,
    рекламодатель — пока публикации нет). Сделки по новым условиям: выход по соглашению сторон или через претензию."""
    with transaction.atomic():
        deal = _lock(pk)
        if deal.on_package_terms:
            raise TransitionError("Отказаться от заключённой сделки в одностороннем порядке нельзя: предложите второй "
                                  "стороне прекратить сделку по соглашению или подайте претензию.")
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
    """Оплата по сроку: сделки «На проверке», у которых наступил payout_due (срок у каждой свой)."""
    now = timezone.now()
    due = [d.pk for d in Deal.objects.filter(status=S.CHECKING) if checking_overdue(d, now)]
    return _run_each(Deal.objects.filter(pk__in=due), lambda pk: complete(pk, actor=None))


def auto_approve_overdue_creatives():
    threshold = timezone.now() - CREATIVE_AUTO_APPROVE_AFTER
    qs = Deal.objects.filter(status=S.ON_APPROVAL, approval_required=False, creative_submitted_at__lte=threshold)
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


def notify_overdue_reviews(now=None):
    """Срок рассмотрения материала истёк, а рекламодатель не ответил — один раз уведомить обе стороны (на отправку).
    Сделка остаётся «На согласовании»: молчание не согласие."""
    now = now or timezone.now()
    candidates = Deal.objects.filter(
        status=S.ON_APPROVAL, approval_required=True, review_overdue_notified_at__isnull=True,
        creative_submitted_at__lte=now - timedelta(days=1),
    )
    overdue = [d.pk for d in candidates if d.review_due and d.review_due <= now]
    deals = _flag_once(Deal.objects.filter(pk__in=overdue), "review_overdue_notified_at", now)
    for deal in deals:
        NotificationService.notify_review_overdue(deal)
    return len(deals)
