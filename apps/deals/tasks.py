from celery import shared_task


@shared_task
def auto_complete_deals():
    """Подтвердить публикацию через 72 часа без ответа рекламодателя (переход — apps/deals/services.py)."""
    from .services import auto_complete_overdue

    return f"Auto-completed {auto_complete_overdue()} deals."


@shared_task
def auto_approve_creative():
    """Согласовать креатив через 48 часов без ответа рекламодателя."""
    from .services import auto_approve_overdue_creatives

    return f"Auto-approved {auto_approve_overdue_creatives()} creatives."


@shared_task
def auto_cancel_overdue_deals():
    """Отменить сделку, 24 часа ждущую оплаты, и вернуть резерв."""
    from .services import auto_cancel_overdue_waiting_payment

    return f"Auto-cancelled {auto_cancel_overdue_waiting_payment()} overdue deals."


@shared_task
def publication_date_reminders():
    """Дата публикации: напоминание исполнителю накануне, уведомление сторонам о просрочке (раз в час)."""
    from .services import notify_overdue_publications, send_publication_reminders

    reminded = send_publication_reminders()
    overdue = notify_overdue_publications()
    return f"Publication reminders: {reminded}; overdue notices: {overdue}."
