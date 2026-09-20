from django.conf import settings


def currency(request):
    """Inject currency settings into every template context."""
    return {
        "currency": {
            "symbol":         getattr(settings, "CURRENCY_SYMBOL", "₽"),
            "code":           getattr(settings, "CURRENCY_CODE", "RUB"),
            "min_withdrawal": getattr(settings, "CURRENCY_MIN_WITHDRAWAL", 500),
            "min_deposit":    getattr(settings, "CURRENCY_MIN_DEPOSIT", 1000),
        }
    }


def bot_protection(request):
    """Публичный ключ Turnstile для виджета капчи (пусто — капча выключена)."""
    return {"turnstile_site_key": getattr(settings, "TURNSTILE_SITE_KEY", "")}


def notifications(request):
    """Inject unread notifications count into every template context (Module 11).

    Используется в base.html для отображения badge на иконке колокольчика.
    Возвращает 0 для анонимных пользователей.
    """
    if request.user.is_authenticated:
        from apps.notifications.models import Notification
        count = Notification.objects.filter(user=request.user, is_read=False).count()
        return {"unread_notifications_count": count}
    return {"unread_notifications_count": 0}
