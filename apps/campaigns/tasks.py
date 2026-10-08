from celery import shared_task


@shared_task
def auto_complete_expired_campaigns():
    """Завершить кампании, у которых прошла дата окончания (раз в час, см. CELERY_BEAT_SCHEDULE)."""
    from .services import complete_expired_campaigns

    return f"Auto-completed {complete_expired_campaigns()} expired campaigns."


@shared_task
def auto_expire_responses_and_offers():
    """Срок ответа на отклик и предложение: напомнить за сутки, истёкшие → EXPIRED (раз в час)."""
    from .services import expire_overdue_responses_and_offers, send_response_offer_reminders

    reminded = send_response_offer_reminders()
    responses, offers = expire_overdue_responses_and_offers()
    return f"Reminders: {reminded}; expired responses: {responses}, offers: {offers}."
