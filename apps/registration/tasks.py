from celery import shared_task


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_blogger_sms_credentials(self, user_id: int, raw_password: str):
    """Отправить блогеру логин/пароль по SMS после успешной верификации OneID.

    Использует apps.registration.services.get_sms_backend() — сейчас это
    заглушка (LogSmsBackend), которая ничего реально не отправляет, только
    логирует. Реальный SMS-провайдер ещё не подключён (нет бюджета/договора,
    см. task/bloger 12092026) — когда появится, меняется только
    get_sms_backend(), эта задача останется без изменений.
    """
    from apps.users.models import User
    from .services import get_sms_backend

    try:
        user = User.objects.select_related("blogger_profile").get(pk=user_id)
    except User.DoesNotExist:
        return

    phone = getattr(getattr(user, "blogger_profile", None), "phone", "")
    if not phone:
        return

    message = f"Логин: {user.email}\nПароль: {raw_password}"
    get_sms_backend().send(phone, message)
