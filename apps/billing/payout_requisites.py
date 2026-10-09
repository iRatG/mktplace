"""Подтверждение реквизитов выплаты блогера (решение бизнеса 09.10.2026, T7/#39).

Заявка → очередь сотрудника → «подтверждена» или «отклонена», по образцу apps.registration IPApplication.
«Подтверждённые реквизиты» пользователя — его последняя заявка со статусом APPROVED, отдельного булева
поля не заводим: новая APPROVED-заявка естественно становится действующей, старые остаются в истории.
"""
from django.db import transaction as db_transaction
from django.utils import timezone

from apps.notifications.service import NotificationService
from apps.profiles.models import BloggerProfile

from .models import PayoutRequisites, WithdrawalRequest

# Какой тип реквизитов требует каждая категория — используется и при подаче заявки, и при проверке.
REQUISITE_TYPE_FOR_CATEGORY = {
    BloggerProfile.Category.INDIVIDUAL: PayoutRequisites.RequisiteType.CARD,
    BloggerProfile.Category.SELF_EMPLOYED: PayoutRequisites.RequisiteType.CARD,
    BloggerProfile.Category.IP: PayoutRequisites.RequisiteType.ACCOUNT,
    BloggerProfile.Category.LEGAL_ENTITY: PayoutRequisites.RequisiteType.ACCOUNT,
}


class PayoutRequisitesError(Exception):
    """Заявку на реквизиты нельзя подать или обработать — текст понятен пользователю."""


def expected_requisite_type(user):
    """Какой тип реквизитов (карта/счёт) ждём от этого блогера по его текущей категории."""
    profile = getattr(user, "blogger_profile", None)
    category = profile.category if profile else BloggerProfile.Category.INDIVIDUAL
    return REQUISITE_TYPE_FOR_CATEGORY[category]


def current_requisites(user):
    """Действующие подтверждённые реквизиты блогера — или None."""
    return (
        PayoutRequisites.objects.filter(user=user, status=PayoutRequisites.Status.APPROVED)
        .order_by("-reviewed_at", "-created_at")
        .first()
    )


def submit_payout_requisites(user, *, card_number="", card_holder_name="", account_number="",
                              bank_mfo="", bank_inn="", bank_name=""):
    """Подать заявку на реквизиты выплаты. Тип определяется текущей категорией блогера, не вводом."""
    identity = getattr(user, "identity_verifications", None)
    if identity is None or not identity.filter(status="verified").exists():
        raise PayoutRequisitesError("Сначала пройдите подтверждение личности.")

    if PayoutRequisites.objects.filter(user=user, status=PayoutRequisites.Status.PENDING).exists():
        raise PayoutRequisitesError("У вас уже есть заявка на реквизиты, которая рассматривается.")

    requisite_type = expected_requisite_type(user)
    fields = {}
    if requisite_type == PayoutRequisites.RequisiteType.CARD:
        card_number = card_number.strip()
        if not card_number.isdigit() or len(card_number) != 16:
            raise PayoutRequisitesError("Номер карты — 16 цифр.")
        profile = getattr(user, "blogger_profile", None)
        identity_record = identity.filter(status="verified").order_by("-verified_at").first()
        expected_holder = identity_record.full_name if identity_record else ""
        if expected_holder and card_holder_name.strip().lower() != expected_holder.strip().lower():
            raise PayoutRequisitesError(
                f"Держатель карты должен совпадать с подтверждённым ФИО: «{expected_holder}»."
            )
        fields = {"card_number": card_number, "card_holder_name": card_holder_name.strip()}
    else:
        if not (account_number.strip() and bank_mfo.strip() and bank_name.strip()):
            raise PayoutRequisitesError("Укажите номер счёта, МФО и название банка.")
        fields = {
            "account_number": account_number.strip(), "bank_mfo": bank_mfo.strip(),
            "bank_inn": bank_inn.strip(), "bank_name": bank_name.strip(),
        }

    return PayoutRequisites.objects.create(user=user, requisite_type=requisite_type, **fields)


@db_transaction.atomic
def approve_payout_requisites(application_pk, staff):
    application = PayoutRequisites.objects.select_for_update().filter(
        pk=application_pk, status=PayoutRequisites.Status.PENDING,
    ).first()
    if application is None:
        raise PayoutRequisitesError("Заявка не найдена или уже обработана.")
    application.status = PayoutRequisites.Status.APPROVED
    application.reviewed_by = staff
    application.reviewed_at = timezone.now()
    application.rejection_reason = ""
    application.save(update_fields=["status", "reviewed_by", "reviewed_at", "rejection_reason", "updated_at"])
    NotificationService.notify_payout_requisites_approved(application.user, application)
    return application


@db_transaction.atomic
def reject_payout_requisites(application_pk, staff, reason):
    if not reason.strip():
        raise PayoutRequisitesError("Укажите причину отклонения.")
    application = PayoutRequisites.objects.select_for_update().filter(
        pk=application_pk, status=PayoutRequisites.Status.PENDING,
    ).first()
    if application is None:
        raise PayoutRequisitesError("Заявка не найдена или уже обработана.")
    application.status = PayoutRequisites.Status.REJECTED
    application.reviewed_by = staff
    application.reviewed_at = timezone.now()
    application.rejection_reason = reason.strip()
    application.save(update_fields=["status", "reviewed_by", "reviewed_at", "rejection_reason", "updated_at"])
    NotificationService.notify_payout_requisites_rejected(application.user, application)
    return application


@db_transaction.atomic
def create_withdrawal_request(blogger, amount):
    """Заявка на вывод — реквизиты берутся снимком из подтверждённых, не из ввода пользователя."""
    from .services import BillingService

    requisites = current_requisites(blogger)
    if requisites is None:
        raise PayoutRequisitesError("Сначала подтвердите реквизиты выплаты.")

    withdrawal = WithdrawalRequest.objects.create(
        blogger=blogger, amount=amount, requisites=requisites.as_snapshot(),
    )
    BillingService.process_withdrawal(withdrawal)
    return withdrawal
