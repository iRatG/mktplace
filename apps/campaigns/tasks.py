from celery import shared_task


@shared_task
def auto_complete_expired_campaigns():
    """Завершить кампании, у которых прошла дата окончания (раз в час, см. CELERY_BEAT_SCHEDULE)."""
    from .services import complete_expired_campaigns

    return f"Auto-completed {complete_expired_campaigns()} expired campaigns."
