from celery import shared_task
from django.utils import timezone


@shared_task
def auto_complete_deals():
    """Auto-complete deals in 'checking' status after 72 hours of no action."""
    from .models import Deal, DealStatusLog

    threshold = timezone.now() - timezone.timedelta(hours=72)
    deals = Deal.objects.filter(
        status=Deal.Status.CHECKING,
        updated_at__lte=threshold,
    )
    count = 0
    for deal in deals:
        DealStatusLog.log(
            deal=deal,
            new_status=Deal.Status.COMPLETED,
            comment="Auto-completed after 72h timeout.",
        )
        deal.status = Deal.Status.COMPLETED
        deal.save(update_fields=["status", "updated_at"])

        from apps.billing.services import BillingService
        BillingService.complete_deal_payment(deal)
        count += 1

    return f"Auto-completed {count} deals."


@shared_task
def auto_approve_creative():
    """Auto-approve creatives that have been on approval for more than 48 hours."""
    from .models import Deal, DealStatusLog

    threshold = timezone.now() - timezone.timedelta(hours=48)
    deals = Deal.objects.filter(
        status=Deal.Status.ON_APPROVAL,
        creative_submitted_at__lte=threshold,
    )
    count = 0
    for deal in deals:
        deal.creative_approved_at = timezone.now()
        deal.save(update_fields=["creative_approved_at"])
        DealStatusLog.log(
            deal=deal,
            new_status=Deal.Status.WAITING_PUBLICATION,
            comment="Auto-approved creative after 48h timeout.",
        )
        deal.status = Deal.Status.WAITING_PUBLICATION
        deal.save(update_fields=["status", "updated_at"])
        count += 1

    return f"Auto-approved {count} creatives."


@shared_task
def auto_cancel_overdue_deals():
    """Cancel deals stuck in waiting_payment status for more than 24 hours."""
    from .models import Deal, DealStatusLog

    threshold = timezone.now() - timezone.timedelta(hours=24)
    deals = Deal.objects.filter(
        status=Deal.Status.WAITING_PAYMENT,
        created_at__lte=threshold,
    )
    count = 0
    for deal in deals:
        DealStatusLog.log(
            deal=deal,
            new_status=Deal.Status.CANCELLED,
            comment="Auto-cancelled due to payment timeout.",
        )
        deal.status = Deal.Status.CANCELLED
        deal.save(update_fields=["status", "updated_at"])
        count += 1

    return f"Auto-cancelled {count} overdue deals."
