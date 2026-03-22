from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.utils import timezone


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_notification(self, user_id: int, notification_type: str, title: str, body: str, deal_id: int = None):
    """Create an in-app notification for the user."""
    from apps.users.models import User
    from .models import Notification

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return

    deal = None
    if deal_id:
        from apps.deals.models import Deal
        try:
            deal = Deal.objects.get(pk=deal_id)
        except Deal.DoesNotExist:
            pass

    Notification.objects.create(
        user=user,
        type=notification_type,
        title=title,
        body=body,
        related_deal=deal,
    )


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_email_notification(self, user_id: int, subject: str, template_name: str, context: dict):
    """Send an email notification to the user."""
    from apps.users.models import User

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return

    # Check user notification settings
    try:
        settings_obj = user.notification_settings
        if not settings_obj.is_enabled(context.get("notification_type", ""), channel="email"):
            return
    except Exception:
        pass  # Send if settings are unavailable

    context["user"] = user
    html_message = render_to_string(template_name, context)
    plain_message = strip_tags(html_message)

    try:
        send_mail(
            subject=subject,
            message=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=html_message,
            fail_silently=False,
        )
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task
def cleanup_old_notifications():
    """Удаляет уведомления старше 90 дней (Модуль 11).

    Запускается периодически через Celery Beat (ежедневно в 03:00).
    На VPS Celery отключён — задача работает только локально.

    Returns:
        int: количество удалённых записей
    """
    from datetime import timedelta
    from .models import Notification
    cutoff = timezone.now() - timedelta(days=90)
    deleted_count, _ = Notification.objects.filter(created_at__lt=cutoff).delete()
    return deleted_count
