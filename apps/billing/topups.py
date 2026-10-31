"""Пополнение по счёту на оплату и возврат свободного остатка рекламодателю.

Деньги двигает только BillingService; здесь — условия, реквизиты, сроки и уведомления. Карт нет: пополнение и возврат идут
переводом, возврат — на счёт, с которого пришли деньги, либо на другой подтверждённый счёт того же плательщика.
"""
import re
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.notifications.service import NotificationService

from .models import BalanceRefund, PaymentInvoice
from .services import BillingService

ACCOUNT_RE = re.compile(r"^\d{20}$")
MFO_RE = re.compile(r"^\d{5}$")


class TopUpError(Exception):
    """Причина для рекламодателя или сотрудника."""


def operator_requisites():
    """Реквизиты оператора для счёта; пока банк не задан в настройках — None, форма счёта скрыта."""
    if not (settings.OPERATOR_ACCOUNT and settings.OPERATOR_MFO and settings.OPERATOR_BANK_NAME):
        return None
    return {
        "name": settings.OPERATOR_NAME, "inn": settings.OPERATOR_INN, "address": settings.OPERATOR_ADDRESS,
        "bank": settings.OPERATOR_BANK_NAME, "account": settings.OPERATOR_ACCOUNT, "mfo": settings.OPERATOR_MFO,
    }


def top_up_error(user):
    """Почему рекламодатель не может создать счёт; None — может."""
    if operator_requisites() is None:
        return "Пополнение откроется, когда будут готовы банковские реквизиты."
    profile = getattr(user, "advertiser_profile", None)
    if not user.is_demo and not (profile and profile.inn):
        return "Счёт можно выставить только рекламодателю с ИНН."
    return None


def create_invoice(user, amount):
    error = top_up_error(user)
    if error:
        raise TopUpError(error)
    if amount <= 0:
        raise TopUpError("Укажите сумму больше нуля.")
    return PaymentInvoice.objects.create(
        advertiser=user, amount=amount, requisites=operator_requisites(),
        expires_at=timezone.now() + timedelta(days=settings.INVOICE_VALID_DAYS),
    )


def _check_account(account, mfo):
    if not ACCOUNT_RE.match(account):
        raise TopUpError("Счёт — 20 цифр.")
    if not MFO_RE.match(mfo):
        raise TopUpError("МФО — 5 цифр.")


def confirm_payment(invoice, staff, amount, received_on, payer_account, payer_mfo):
    """Сотрудник отметил поступление по выписке: зачисляется сумма из выписки."""
    payer_account, payer_mfo = payer_account.strip(), payer_mfo.strip()
    _check_account(payer_account, payer_mfo)
    try:
        BillingService.credit_invoice(invoice, amount, received_on, payer_account, payer_mfo, staff)
    except ValueError as e:
        raise TopUpError(str(e)) from e
    invoice.refresh_from_db()
    NotificationService.notify_balance_topped_up(invoice)
    return invoice


def expire_overdue():
    """Неоплаченные счета после срока. Поздний перевод сотрудник всё равно может зачислить."""
    overdue = list(PaymentInvoice.objects.filter(status=PaymentInvoice.Status.ISSUED, expires_at__lt=timezone.now()))
    for invoice in overdue:
        PaymentInvoice.objects.filter(pk=invoice.pk).update(status=PaymentInvoice.Status.EXPIRED)
        NotificationService.notify_invoice_expired(invoice)
    return len(overdue)


def source_account(user):
    """Счёт и МФО последнего поступления — возврат идёт на него."""
    paid = PaymentInvoice.objects.filter(advertiser=user, status=PaymentInvoice.Status.PAID).order_by("-confirmed_at").first()
    return (paid.payer_account, paid.payer_mfo) if paid else ("", "")


def request_refund(user, amount, account, mfo):
    account, mfo = account.strip(), mfo.strip()
    _check_account(account, mfo)
    try:
        refund = BillingService.request_balance_refund(user, amount, account, mfo)
    except ValueError as e:
        raise TopUpError(str(e)) from e
    NotificationService.notify_refund_requested(refund)
    return refund


def _guarded(call, *args, notify=True):
    try:
        refund = call(*args)
    except ValueError as e:
        raise TopUpError(str(e)) from e
    if notify:
        NotificationService.notify_refund_changed(refund)
    return refund


def cancel_refund(refund, user):
    if refund.advertiser_id != user.pk:
        raise TopUpError("Заявка не найдена.")
    return _guarded(BillingService.cancel_balance_refund, refund, notify=False)


def complete_refund(refund, staff):
    return _guarded(BillingService.complete_balance_refund, refund, staff)


def reject_refund(refund, staff, reason):
    return _guarded(BillingService.reject_balance_refund, refund, staff, reason)


def mark_refund_sent(refund, staff):
    """Распоряжение передано банку: заявку больше нельзя отозвать."""
    with transaction.atomic():
        locked = BalanceRefund.objects.select_for_update().get(pk=refund.pk)
        if locked.status != BalanceRefund.Status.REQUESTED:
            raise TopUpError("Передать в банк можно только принятую заявку.")
        locked.status = BalanceRefund.Status.SENT
        locked.sent_at = timezone.now()
        locked.processed_by = staff
        locked.save(update_fields=["status", "sent_at", "processed_by"])
    NotificationService.notify_refund_changed(locked)
    return locked


def ask_for_details(refund, staff, note):
    """Сведений не хватает: срок передачи в банк считается заново, когда рекламодатель их пришлёт."""
    note = (note or "").strip()
    if not note:
        raise TopUpError("Напишите, каких сведений не хватает.")
    with transaction.atomic():
        locked = BalanceRefund.objects.select_for_update().get(pk=refund.pk)
        if locked.status != BalanceRefund.Status.REQUESTED:
            raise TopUpError("Сведения запрашиваются по принятой заявке.")
        locked.status = BalanceRefund.Status.NEEDS_INFO
        locked.staff_comment = note
        locked.processed_by = staff
        locked.save(update_fields=["status", "staff_comment", "processed_by"])
    NotificationService.notify_refund_changed(locked)
    return locked


def update_details(refund, user, account, mfo):
    account, mfo = account.strip(), mfo.strip()
    _check_account(account, mfo)
    with transaction.atomic():
        locked = BalanceRefund.objects.select_for_update().get(pk=refund.pk)
        if locked.advertiser_id != user.pk or locked.status != BalanceRefund.Status.NEEDS_INFO:
            raise TopUpError("Заявку сейчас нельзя изменить.")
        locked.account, locked.mfo = account, mfo
        locked.other_account = (account, mfo) != source_account(user)
        locked.status = BalanceRefund.Status.REQUESTED
        locked.set_deadlines()
        locked.save(update_fields=["account", "mfo", "other_account", "status", "due_at", "info_due_at"])
    NotificationService.notify_refund_requested(locked)
    return locked
