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
