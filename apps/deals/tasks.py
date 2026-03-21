from celery import shared_task
from django.db import transaction as db_transaction
from django.utils import timezone


@shared_task
def auto_complete_deals():
    """Auto-complete deals in checking status after 72 hours of no action."""
    from apps.billing.services import BillingService
    from .models import Deal, DealStatusLog

    threshold = timezone.now() - timezone.timedelta(hours=72)
    deal_ids = list(
        Deal.objects.filter(
            status=Deal.Status.CHECKING,
            updated_at__lte=threshold,
        ).values_list("pk", flat=True)
    )
    count = 0
    for deal_id in deal_ids:
        try:
            with db_transaction.atomic():
                # select_for_update предотвращает race condition с ручным подтверждением
                deal = Deal.objects.select_for_update().get(
                    pk=deal_id, status=Deal.Status.CHECKING
                )
                deal.status = Deal.Status.COMPLETED
                deal.save(update_fields=["status", "updated_at"])
                DealStatusLog.log(
                    deal=deal,
                    new_status=Deal.Status.COMPLETED,
                    comment="Auto-completed after 72h timeout.",
                )
                BillingService.complete_deal_payment(deal)
                count += 1
        except Deal.DoesNotExist:
            # Сделка уже обработана другим процессом — пропускаем
            pass

    return f"Auto-completed {count} deals."


@shared_task
def auto_approve_creative():
    """Auto-approve creatives that have been on approval for more than 48 hours."""
    from .models import Deal, DealStatusLog

    threshold = timezone.now() - timezone.timedelta(hours=48)
    deal_ids = list(
        Deal.objects.filter(
            status=Deal.Status.ON_APPROVAL,
            creative_submitted_at__lte=threshold,
        ).values_list("pk", flat=True)
    )
    count = 0
    for deal_id in deal_ids:
        try:
            with db_transaction.atomic():
                deal = Deal.objects.select_for_update().get(
                    pk=deal_id, status=Deal.Status.ON_APPROVAL
                )
                deal.creative_approved_at = timezone.now()
                deal.status = Deal.Status.WAITING_PUBLICATION
                deal.save(update_fields=["creative_approved_at", "status", "updated_at"])
                DealStatusLog.log(
                    deal=deal,
                    new_status=Deal.Status.WAITING_PUBLICATION,
                    comment="Auto-approved creative after 48h timeout.",
                )
                count += 1
        except Deal.DoesNotExist:
            pass

    return f"Auto-approved {count} creatives."


@shared_task
def auto_cancel_overdue_deals():
    """Cancel deals stuck in waiting_payment status for more than 24 hours."""
    from apps.billing.services import BillingService
    from .models import Deal, DealStatusLog

    threshold = timezone.now() - timezone.timedelta(hours=24)
    deal_ids = list(
        Deal.objects.filter(
            status=Deal.Status.WAITING_PAYMENT,
            created_at__lte=threshold,
        ).values_list("pk", flat=True)
    )
    count = 0
    for deal_id in deal_ids:
        try:
            with db_transaction.atomic():
                deal = Deal.objects.select_for_update().get(
                    pk=deal_id, status=Deal.Status.WAITING_PAYMENT
                )
                deal.status = Deal.Status.CANCELLED
                deal.save(update_fields=["status", "updated_at"])
                DealStatusLog.log(
                    deal=deal,
                    new_status=Deal.Status.CANCELLED,
                    comment="Auto-cancelled due to payment timeout.",
                )
                # ИСПРАВЛЕНО: возвращаем зарезервированные средства рекламодателю
                BillingService.release_funds(deal)
                count += 1
        except Deal.DoesNotExist:
            pass

    return f"Auto-cancelled {count} overdue deals."
