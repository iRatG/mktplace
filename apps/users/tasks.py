import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone


def _send(user, letter, to=None):
    """Отправка фирменного письма из задачи (apps/notifications/emails.py); ответ на письмо — в поддержку.

    Адрес — `to` (подтверждение логина) или адрес для писем пользователя (`emails.address_for`); нет адреса — нет письма.
    """
    from apps.notifications import emails

    to = to or emails.address_for(user)
    if not to:
        return
    subject, html, text = letter
    message = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [to],
                                     reply_to=[settings.SUPPORT_EMAIL])
    message.attach_alternative(html, "text/html")
    message.send(fail_silently=False)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_confirmation_email(self, user_id: int):
    """Create email confirmation token and send link to the user."""
    from apps.notifications import emails
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
    try:
        _send(user, emails.email_confirmation(user, confirmation_url), to=user.email)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_password_reset_email(self, user_id: int):
    """Create password reset token and send link to the user."""
    from apps.notifications import emails
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
    try:
        _send(user, emails.password_reset(user, reset_url))
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_welcome_email(self, user_id: int):
    """Приветственное письмо после подтверждения email: своё для рекламодателя и для исполнителя.

    Идёт на адрес для писем (`emails.address_for`): без него (служебный логин, демо) письма нет.
    """
    from apps.notifications import emails
    from .models import User

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return
    if not emails.address_for(user):
        return
    try:
        _send(user, emails.welcome(user))
    except Exception as exc:
        raise self.retry(exc=exc)


def queue_welcome_email(user_id: int) -> None:
    """Ставит приветственное письмо в очередь. Сбой очереди (например, Redis
    недоступен) не должен ломать подтверждение email — только пишется в лог."""
    try:
        send_welcome_email.delay(user_id)
    except Exception:
        logging.getLogger(__name__).exception("Не удалось поставить welcome-письмо в очередь (user %s)", user_id)
