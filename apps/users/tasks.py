from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_confirmation_email(self, user_id: int, token: str):
    """Send email confirmation link to the user."""
    from .models import User

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return

    confirmation_url = (
        f"{settings.FRONTEND_URL}/email-confirm/{token}/"
    )

    subject = "Confirm your email address"
    html_message = render_to_string(
        "emails/email_confirmation.html",
        {
            "user": user,
            "confirmation_url": confirmation_url,
        },
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
def send_password_reset_email(self, user_id: int, token: str):
    """Send password reset link to the user."""
    from .models import User

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return

    reset_url = f"{settings.FRONTEND_URL}/password-reset/confirm/?token={token}"

    subject = "Reset your password"
    html_message = render_to_string(
        "emails/password_reset.html",
        {
            "user": user,
            "reset_url": reset_url,
        },
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
