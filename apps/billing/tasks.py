from celery import shared_task


@shared_task
def einvoice_reminders():
    """Напомнить рекламодателю подтвердить ЭСФ исполнителя (один раз, ESF_REMINDER_DAYS) — #61."""
    from .esf import send_reminders

    return f"E-invoice reminders: {send_reminders()}."


@shared_task
def sync_einvoices():
    """Статусы ЭСФ у внешнего провайдера (оператор ЭДО); ручной провайдер — ничего не делает."""
    from .esf import sync_waiting

    return f"E-invoices synced: {sync_waiting()}."
