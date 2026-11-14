"""Отчёт комиссионера рекламодателю за месяц (ПР 8.9, редакция 06.11.2026).

Не позднее 5 рабочих дней после окончания месяца Оператор представляет рекламодателю отчёт: сделки, стоимость услуг,
выплаты, зачёты, комиссия рекламодателя, возвраты и остаток; плата исполнителя — отдельно. Рекламодатель вправе направить
мотивированные возражения в течение 10 рабочих дней после предоставления; без возражений отчёт считается принятым.

Суммы считает `metrics.advertiser_month_report`. Ежедневная задача `commission_reports` формирует отчёты за прошлый месяц
(идемпотентно — в первый же день месяца) и закрывает истёкшие сроки возражений.
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.notifications.service import NotificationService

from .models import CommissionReport, Transaction

OBJECTION_WORKING_DAYS = 10


class ReportError(Exception):
    """Действие с отчётом невозможно — текст понятен пользователю."""


def previous_month(today):
    first = today.replace(day=1)
    last = first - timedelta(days=1)
    return last.replace(day=1)


def _bounds(period):
    from . import metrics

    next_month = (period.replace(day=28) + timedelta(days=4)).replace(day=1)
    return metrics.period_bounds(period, next_month - timedelta(days=1))


def build(advertiser, period):
    """Создать отчёт за месяц period (первое число), если его ещё нет. Возвращает (report, created)."""
    from apps.campaigns.validation import working_days_after

    from . import metrics

    existing = CommissionReport.objects.filter(advertiser=advertiser, period=period).first()
    if existing:
        return existing, False
    start, end = _bounds(period)
    data = metrics.advertiser_month_report(advertiser, start, end)
    now = timezone.now()
    report = CommissionReport.objects.create(
        advertiser=advertiser, period=period, data=data,
        objection_until=working_days_after(now, OBJECTION_WORKING_DAYS),
    )
    NotificationService.notify_commission_report_ready(report)
    return report, True


def generate_for_previous_month(today=None):
    """Отчёты за прошлый месяц всем рекламодателям, у кого в нём были движения денег. Возвращает число новых."""
    from apps.users.models import User

    today = today or timezone.localdate()
    period = previous_month(today)
    start, end = _bounds(period)
    ids = (
        Transaction.objects.filter(created_at__gte=start, created_at__lt=end, wallet__user__role=User.Role.ADVERTISER)
        .values_list("wallet__user_id", flat=True).distinct()
    )
    created = 0
    for advertiser in User.objects.filter(pk__in=list(ids)):
        _, new = build(advertiser, period)
        created += new
    return created


def accept_overdue(now=None):
    """Срок возражений прошёл без возражения — отчёт принят (ПР 8.9). Возвращает число принятых."""
    now = now or timezone.now()
    return CommissionReport.objects.filter(
        status=CommissionReport.Status.SENT, objection_until__lte=now,
    ).update(status=CommissionReport.Status.ACCEPTED, accepted_at=now)


def object_to(report_pk, actor, text):
    """Рекламодатель направляет мотивированное возражение, пока не истёк срок."""
    text = (text or "").strip()
    if not text:
        raise ReportError("Опишите возражение: какие операции и почему.")
    with transaction.atomic():
        report = CommissionReport.objects.select_for_update().filter(pk=report_pk, advertiser=actor).first()
        if report is None:
            raise ReportError("Отчёт не найден.")
        if report.status != CommissionReport.Status.SENT:
            raise ReportError("Возражение можно направить только по отчёту, который ещё не принят.")
        if timezone.now() > report.objection_until:
            raise ReportError(f"Срок возражений истёк {timezone.localtime(report.objection_until):%d.%m.%Y}.")
        report.status = CommissionReport.Status.OBJECTED
        report.objection_text = text[:5000]
        report.objected_at = timezone.now()
        report.save(update_fields=["status", "objection_text", "objected_at"])
    NotificationService.notify_commission_report_objected(report)
    return report


def resolve(report_pk, staff, comment):
    """Сотрудник рассмотрел возражение и ответил рекламодателю."""
    comment = (comment or "").strip()
    if staff is None or not staff.is_staff:
        raise ReportError("Рассмотреть возражение может только сотрудник.")
    if not comment:
        raise ReportError("Напишите ответ рекламодателю.")
    with transaction.atomic():
        report = CommissionReport.objects.select_for_update().filter(pk=report_pk).first()
        if report is None or report.status != CommissionReport.Status.OBJECTED:
            raise ReportError("Возражение не найдено или уже рассмотрено.")
        report.status = CommissionReport.Status.RESOLVED
        report.staff_comment = comment[:5000]
        report.resolved_by = staff
        report.resolved_at = timezone.now()
        report.save(update_fields=["status", "staff_comment", "resolved_by", "resolved_at"])
    NotificationService.notify_commission_report_resolved(report)
    return report


def period_label(period):
    months = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь",
              "ноябрь", "декабрь"]
    return f"{months[period.month - 1]} {period.year}"

