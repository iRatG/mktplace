from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_confirmation_email(self, user_id: int):
    """Create email confirmation token and send link to the user."""
    from .models import EmailConfirmationToken, User

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return

    token = EmailConfirmationToken.objects.create(
        user=user,
        expires_at=timezone.now() + timezone.timedelta(hours=24),
    )

    confirmation_url = f"{settings.FRONTEND_URL}/confirm-email/{token.token}/"

    subject = "Подтвердите email — Mktplace"
    html_message = render_to_string(
        "emails/email_confirmation.html",
        {"user": user, "confirmation_url": confirmation_url},
    )
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


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_password_reset_email(self, user_id: int):
    """Create password reset token and send link to the user."""
    from .models import PasswordResetToken, User

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return

    token = PasswordResetToken.objects.create(
        user=user,
        expires_at=timezone.now() + timezone.timedelta(hours=1),
    )

    reset_url = f"{settings.FRONTEND_URL}/password-reset/{token.token}/"

    subject = "Сброс пароля — Mktplace"
    html_message = render_to_string(
        "emails/password_reset.html",
        {"user": user, "reset_url": reset_url},
    )
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
