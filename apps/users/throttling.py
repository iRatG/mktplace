"""DRF-throttling для /api/v1/ с возможностью глобального отключения.

Свои подклассы нужны только ради флага RATELIMIT_ENABLED: в тестах счётчики
не должны копиться между запросами с одного адреса. Сами лимиты задаются в
REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'] (см. config/settings/base.py).
"""
from django.conf import settings
from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle, UserRateThrottle

from . import blocklist
from .security import client_ip


class _Switchable:
    def allow_request(self, request, view):
        if not settings.RATELIMIT_ENABLED:
            return True
        allowed = super().allow_request(request, view)
        if not allowed:
            blocklist.record_strike(client_ip(request), f"api:{getattr(self, 'scope', None) or 'throttle'}")
        return allowed


class AnonThrottle(_Switchable, AnonRateThrottle):
    pass


class UserThrottle(_Switchable, UserRateThrottle):
    pass


class ScopedThrottle(_Switchable, ScopedRateThrottle):
    """Действует только на вью с атрибутом throttle_scope."""
