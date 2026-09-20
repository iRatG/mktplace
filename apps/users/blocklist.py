"""Блокировка IP: ручные записи (Django admin) и автоблок по правилу.

Правило автоблока: каждое срабатывание защиты от ботов (превышен лимит, сработал
honeypot, не пройдена капча) даёт IP «штраф»; AUTOBLOCK_STRIKES штрафов за
AUTOBLOCK_WINDOW секунд — блокировка на AUTOBLOCK_HOURS часов. Honeypot весит
больше обычного превышения лимита: человек скрытое поле не заполнит.

Автоблок выключен по умолчанию (AUTOBLOCK_ENABLED=False) и включается только когда
приложение недоступно в обход nginx: адрес берётся из X-Real-IP, и пока порт
приложения открыт наружу, кто угодно может подставить в заголовок чужой IP и
заблокировать его. Ручные блокировки работают всегда.
"""
import hashlib
import ipaddress
import logging

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

CACHE_TTL = 60  # секунд: и «заблокирован», и «не заблокирован» кэшируются, чтобы не ходить в БД на каждый запрос


def _blocked_key(ip: str) -> str:
    return f"blockedip:{ip}"


def _strikes_key(ip: str) -> str:
    return "strikes:" + hashlib.sha256(ip.encode()).hexdigest()[:32]


def invalidate(ip: str) -> None:
    cache.delete(_blocked_key(ip))


def is_blocked(ip: str) -> bool:
    cached = cache.get(_blocked_key(ip))
    if cached is not None:
        return cached
    from .models import BlockedIP

    try:
        blocked = BlockedIP.objects.filter(ip=ip).filter(
            Q(blocked_until__isnull=True) | Q(blocked_until__gt=timezone.now())
        ).exists()
    except DatabaseError:
        # Например, миграция с таблицей ещё не применена (окно между «up -d» и «migrate»).
        # Блок по IP — дополнительная защита: при сбое пропускаем запрос, а не роняем сайт.
        logger.error("Не удалось проверить список заблокированных IP, запрос пропущен", exc_info=True)
        return False
    cache.set(_blocked_key(ip), blocked, timeout=CACHE_TTL)
    return blocked


def is_blockable(ip: str) -> bool:
    """Автоблок только для публичных адресов: локальные, приватные и адреса
    прокси блокировать нельзя — под ними может сидеть весь сайт."""
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


def record_strike(ip: str, scope: str, weight: int = 1) -> bool:
    """Засчитывает IP штраф. True — IP только что заблокирован автоматически."""
    if not (settings.RATELIMIT_ENABLED and settings.AUTOBLOCK_ENABLED) or not is_blockable(ip):
        return False
    key = _strikes_key(ip)
    if cache.add(key, weight, timeout=settings.AUTOBLOCK_WINDOW):
        count = weight
    else:
        try:
            count = cache.incr(key, weight)
        except ValueError:  # ключ истёк между add и incr
            cache.set(key, weight, timeout=settings.AUTOBLOCK_WINDOW)
            count = weight
    if count < settings.AUTOBLOCK_STRIKES:
        return False
    return _auto_block(ip, scope, count)


def _auto_block(ip: str, scope: str, count: int) -> bool:
    from .models import BlockedIP

    existing = BlockedIP.objects.filter(ip=ip).first()
    if existing and existing.is_active:
        return False  # уже заблокирован (в том числе вручную — ручную запись не трогаем)
    until = timezone.now() + timezone.timedelta(hours=settings.AUTOBLOCK_HOURS)
    reason = f"Автоблок: {count} срабатываний защиты за {settings.AUTOBLOCK_WINDOW // 60} мин (последнее: {scope})"
    BlockedIP.objects.update_or_create(
        ip=ip, defaults={"source": BlockedIP.Source.AUTO, "reason": reason[:255], "blocked_until": until},
    )
    cache.delete(_strikes_key(ip))
    logger.warning("IP %s заблокирован автоматически до %s: %s", ip, until, reason)
    return True
