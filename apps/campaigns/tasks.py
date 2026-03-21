from celery import shared_task
from django.utils import timezone


@shared_task
def auto_complete_expired_campaigns():
    """Automatically complete campaigns whose end_date has passed."""
    from .models import Campaign

    now = timezone.now().date()
    updated = Campaign.objects.filter(
        status=Campaign.Status.ACTIVE,
        end_date__lt=now,
    ).update(status=Campaign.Status.COMPLETED)

    return f"Auto-completed {updated} expired campaigns."
