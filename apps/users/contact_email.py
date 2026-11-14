"""Адрес для писем (Р27).

Логин юрлица — служебный адрес по ИНН, логин блогера — служебный адрес по телефону: на них письма не уходят. Поэтому
пользователь указывает в профиле «Email для писем» и подтверждает его по ссылке из письма. Подтверждённый адрес —
`User.contact_email`; все фирменные письма идут на `apps.notifications.emails.address_for(user)`.

Пока новый адрес не подтверждён, письма идут на прежний: адрес-кандидат живёт только в токене подтверждения
(`EmailConfirmationToken.email`). Первое подтверждение у пользователя без адреса для писем — повод для приветствия.
"""
from datetime import timedelta

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.notifications import emails

from . import security
from .models import EmailConfirmationToken, User

CONFIRM_HOURS = 24
REQUEST_LIMIT, REQUEST_WINDOW = 5, 3600  # писем подтверждения на пользователя в час: чужой ящик не завалить


class ContactEmailError(Exception):
    """Текст ошибки — для пользователя."""


def _open_tokens(user):
    return EmailConfirmationToken.objects.filter(user=user, is_used=False).exclude(email="")


def pending(user):
    """Адрес, который ждёт подтверждения (последний действующий токен), или None."""
    token = _open_tokens(user).filter(expires_at__gt=timezone.now()).order_by("-created_at").first()
    return token.email if token else None


def request_change(user, email):
    """Отправить ссылку подтверждения на новый адрес. Письма идут на прежний адрес до подтверждения."""
    email = (email or "").strip().lower()
    if not emails.is_real_address(email):
        raise ContactEmailError("Укажите адрес почты, который вы читаете.")
    if email == user.contact_email:
        raise ContactEmailError("Этот адрес уже подтверждён — письма приходят на него.")
    if security.hit("contact_email", str(user.pk), REQUEST_LIMIT, REQUEST_WINDOW):
        raise ContactEmailError(security.MSG_TOO_MANY)
    with transaction.atomic():
        _open_tokens(user).update(is_used=True)
        token = EmailConfirmationToken.objects.create(
            user=user, email=email, expires_at=timezone.now() + timedelta(hours=CONFIRM_HOURS),
        )
        url = emails.site_url(reverse("web:email_confirm", args=[token.token]))
        emails.send(email, emails.contact_email_confirmation, user, url)
    return token


def confirm(token):
    """Подтвердить адрес по токену (токен уже проверен на срок и использование). Возвращает пользователя."""
    with transaction.atomic():
        token = EmailConfirmationToken.objects.select_for_update().get(pk=token.pk)
        user = User.objects.select_for_update().get(pk=token.user_id)
        first = not emails.address_for(user)
        user.contact_email = token.email
        user.save(update_fields=["contact_email"])
        _open_tokens(user).update(is_used=True)
        if first:
            emails.send_to(user, emails.welcome, user)
    return user


def remove(user):
    """Не присылать письма: адрес для писем и ожидающие подтверждения удаляются."""
    with transaction.atomic():
        user.contact_email = ""
        user.save(update_fields=["contact_email"])
        _open_tokens(user).update(is_used=True)
