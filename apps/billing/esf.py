"""ЭСФ (электронная счёт-фактура) по оплаченной сделке — документы и их статусы, без денег (#61, 13.10.2026).

Ответ бизнеса 13.10: выплата ИП, юрлицу и самозанятому — после того, как рекламодатель подтвердил ЭСФ исполнителя;
платформа выставляет рекламодателю свою ЭСФ на комиссию в момент выплаты. Статусы сейчас вносит сотрудник вручную
(provider «manual»); оператор ЭДО подключается новым классом провайдера (#63) — вызывающий код не меняется.
Деньги не трогаем: выплату блокирует `WithdrawalRequest.blocked_reasons`, который смотрит на статус ЭСФ.
"""
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction as db_transaction
from django.utils import timezone

from apps.notifications.service import NotificationService

from .models import EInvoice

Status = EInvoice.Status
Kind = EInvoice.Kind

# Какие статусы сотрудник (или провайдер) может поставить ЭСФ каждого вида.
ALLOWED_STATUSES = {
    Kind.PERFORMER: (Status.WAITING, Status.CONFIRMED, Status.REJECTED, Status.MISMATCH, Status.NOT_REQUIRED),
    Kind.PLATFORM: (Status.TO_ISSUE, Status.CONFIRMED),
}


class EsfError(Exception):
    """Статус ЭСФ нельзя изменить — текст понятен сотруднику."""


def esf_required_for(blogger):
    """Нужна ли ЭСФ исполнителя для выплаты этому блогеру — по его категории (ESF_REQUIRED_CATEGORIES)."""
    profile = getattr(blogger, "blogger_profile", None)
    category = profile.category if profile else "individual"
    return category in settings.ESF_REQUIRED_CATEGORIES


def open_for_deal(deal, amount):
    """ЭСФ исполнителя при оплате сделки: «ждёт подтверждения» или «не требуется». Идемпотентно.

    Возвращает (einvoice, created). Вызывается внутри транзакции оплаты сделки (apps/deals/services.py).
    """
    status = Status.WAITING if esf_required_for(deal.blogger) else Status.NOT_REQUIRED
    return EInvoice.objects.get_or_create(
        deal=deal, kind=Kind.PERFORMER,
        defaults={"status": status, "contract_number": deal.contract_number, "amount": amount},
    )


def open_platform_einvoice(payout):
    """ЭСФ платформы на комиссию — «к выставлению» после проведения выплаты по сделке (ответ 13.10, вопрос 9).

    Без комиссии (SpecialTariff 0%) документа нет. Идемпотентно; возвращает ЭСФ или None.
    """
    deal = payout.deal
    if deal is None:
        return None
    from . import metrics

    commission = metrics.deal_commission(deal)
    if commission <= 0:
        return None
    einvoice, _ = EInvoice.objects.get_or_create(
        deal=deal, kind=Kind.PLATFORM,
        defaults={"status": Status.TO_ISSUE, "contract_number": deal.contract_number, "amount": commission},
    )
    return einvoice


@db_transaction.atomic
def set_status(einvoice_pk, staff, *, status, number="", note="", external_id=""):
    """Сотрудник (или провайдер, staff=None) ставит статус ЭСФ. Отклонение и расхождение — уведомление сотрудникам."""
    einvoice = (
        EInvoice.objects.select_for_update().select_related("deal__campaign", "deal__advertiser", "deal__blogger")
        .filter(pk=einvoice_pk).first()
    )
    if einvoice is None:
        raise EsfError("ЭСФ не найдена.")
    if status not in ALLOWED_STATUSES[einvoice.kind]:
        raise EsfError("Недопустимый статус для этого вида ЭСФ.")
    number = (number or "").strip()
    if status == Status.CONFIRMED and not (number or einvoice.number):
        raise EsfError("Укажите номер ЭСФ.")
    einvoice.status = status
    if number:
        einvoice.number = number
    if external_id:
        einvoice.external_id = external_id
    einvoice.note = (note or "").strip()
    einvoice.checked_by = staff
    einvoice.status_changed_at = timezone.now()
    einvoice.save(update_fields=["status", "number", "external_id", "note", "checked_by", "status_changed_at",
                                 "updated_at"])
    if status in (Status.REJECTED, Status.MISMATCH):
        # Уведомление — строка в той же БД: откатится вместе с транзакцией, отдельный on_commit не нужен.
        NotificationService.notify_esf_problem(einvoice)
    return einvoice


# ── Провайдер статусов ────────────────────────────────────────────────────────

@dataclass
class EsfStatusResult:
    status: str
    number: str = ""
    external_id: str = ""
    note: str = ""


class EsfProvider:
    """Источник статусов ЭСФ. fetch_status возвращает EsfStatusResult или None (ничего нового)."""

    name = "manual"

    def fetch_status(self, einvoice):
        return None


class ManualEsfProvider(EsfProvider):
    """Статусы вносит сотрудник — запрашивать нечего."""


PROVIDERS = {"manual": ManualEsfProvider}


def get_esf_provider(name):
    cls = PROVIDERS.get(name)
    return cls() if cls else None


def sync_waiting():
    """Опрос внешних провайдеров по незакрытым ЭСФ (задача beat). Ручной провайдер пропускается. Возвращает число
    обновлённых."""
    updated = 0
    qs = EInvoice.objects.filter(status__in=(Status.WAITING, Status.TO_ISSUE)).exclude(provider="manual")
    for einvoice in qs:
        provider = get_esf_provider(einvoice.provider)
        if provider is None:
            continue
        result = provider.fetch_status(einvoice)
        if result is None or result.status == einvoice.status:
            continue
        set_status(einvoice.pk, None, status=result.status, number=result.number, note=result.note,
                   external_id=result.external_id)
        updated += 1
    return updated


def send_reminders(now=None):
    """Одно напоминание рекламодателю подтвердить ЭСФ через ESF_REMINDER_DAYS без подтверждения (задача beat)."""
    now = now or timezone.now()
    threshold = now - timedelta(days=settings.ESF_REMINDER_DAYS)
    with db_transaction.atomic():
        items = list(
            EInvoice.objects.filter(kind=Kind.PERFORMER, status=Status.WAITING, reminder_sent_at__isnull=True,
                                    created_at__lte=threshold)
            .select_for_update(of=("self",)).select_related("deal__campaign", "deal__advertiser")
        )
        EInvoice.objects.filter(pk__in=[e.pk for e in items]).update(reminder_sent_at=now)
    for einvoice in items:
        NotificationService.notify_esf_to_confirm(einvoice.deal.advertiser, einvoice.deal, einvoice, reminder=True)
    return len(items)
