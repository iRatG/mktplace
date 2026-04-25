from celery import shared_task

from django.utils import timezone
from datetime import timedelta


@shared_task
def check_permit_expiry():
    """Ежедневная задача контроля сроков разрешительных документов (REQ-2).

    - За 30 дней до expires_at → уведомление пользователю
    - После истечения expires_at → статус EXPIRED + SUSPENDED для площадок в этой категории
    """
    from apps.platforms.models import PermitDocument, Platform
    from apps.notifications.models import Notification

    today = timezone.now().date()
    warn_date = today + timedelta(days=30)

    # Уведомить об истекающих документах (expires_at через 30 дней)
    expiring = PermitDocument.objects.filter(
        status=PermitDocument.Status.APPROVED,
        expires_at=warn_date,
    ).select_related("user", "category")

    for permit in expiring:
        Notification.objects.get_or_create(
            user=permit.user,
            type=Notification.Type.SYSTEM,
            title=f"Документ истекает через 30 дней: {permit.category.name}",
            defaults={
                "body": (
                    f"Разрешительный документ «{permit.get_doc_type_display()} № {permit.doc_number}» "
                    f"для категории «{permit.category.name}» истекает {permit.expires_at}. "
                    f"Загрузите обновлённый документ, чтобы избежать приостановки площадок."
                )
            },
        )

    # Пометить истёкшие документы и приостановить площадки
    expired = PermitDocument.objects.filter(
        status=PermitDocument.Status.APPROVED,
        expires_at__lt=today,
    ).select_related("user", "category")

    for permit in expired:
        permit.status = PermitDocument.Status.EXPIRED
        permit.save(update_fields=["status", "updated_at"])

        # Приостановить APPROVED площадки пользователя в данной категории
        platforms_to_suspend = Platform.objects.filter(
            blogger=permit.user,
            status=Platform.Status.APPROVED,
            categories=permit.category,
        )
        platforms_to_suspend.update(status=Platform.Status.SUSPENDED)

        Notification.objects.create(
            user=permit.user,
            type=Notification.Type.SYSTEM,
            title=f"Документ истёк, площадки приостановлены: {permit.category.name}",
            body=(
                f"Разрешительный документ «{permit.get_doc_type_display()} № {permit.doc_number}» "
                f"для категории «{permit.category.name}» истёк. "
                f"Площадки в этой категории приостановлены. Загрузите новый документ."
            ),
        )

    return f"Checked: {expiring.count()} expiring, {expired.count()} expired"
