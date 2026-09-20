"""Защита публичных форм и API от ботов и перебора.

Три независимых слоя, все используют общий Redis-кэш Django:
- лимиты на число попыток (`hit` / `is_limited`) по IP и по «личным» данным
  (email, телефон, ПИНФЛ);
- скрытое поле-ловушка (honeypot) — простые боты заполняют все поля подряд;
- капча Cloudflare Turnstile — включается, только если заданы
  TURNSTILE_SITE_KEY и TURNSTILE_SECRET_KEY (иначе форма работает как раньше).

Лимиты можно целиком отключить настройкой RATELIMIT_ENABLED=False (в тестах
по умолчанию так и есть, чтобы счётчики не накапливались между тестами).
"""
import hashlib
import ipaddress
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.cache import cache

from . import blocklist

logger = logging.getLogger(__name__)

# Имя нарочно нейтральное: поля вроде website/url/phone браузеры и менеджеры паролей
# заполняют сами, и живой человек был бы принят за бота.
HONEYPOT_FIELD = "hp_note"
HONEYPOT_STRIKES = 5  # человек скрытое поле не заполнит, поэтому штраф выше, чем за превышение лимита
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TURNSTILE_RESPONSE_FIELD = "cf-turnstile-response"
# Коды ошибок siteverify, означающие проблему на нашей стороне, а не «плохой токен».
TURNSTILE_OUR_SIDE_ERRORS = {"missing-input-secret", "invalid-input-secret", "bad-request", "internal-error"}


def client_ip(request) -> str:
    """IP клиента для лимитов.

    Берём X-Real-IP, а не первый элемент X-Forwarded-For: nginx всегда
    перезаписывает X-Real-IP значением $remote_addr, тогда как X-Forwarded-For
    клиент может подделать (`proxy_add_x_forwarded_for` дописывает адрес в
    конец, не стирая присланное клиентом) и тем самым обойти лимит.
    Доверять этому заголовку можно только пока приложение доступно исключительно
    через nginx (порт приложения не должен быть открыт наружу).
    """
    candidate = request.META.get("HTTP_X_REAL_IP", "").strip()
    try:
        ipaddress.ip_address(candidate)
        return candidate
    except ValueError:
        return request.META.get("REMOTE_ADDR") or "unknown"


def _key(scope: str, ident: str) -> str:
    # Хэш — чтобы email/телефон/ПИНФЛ не лежали в Redis открытым текстом.
    digest = hashlib.sha256(str(ident).encode("utf-8")).hexdigest()[:32]
    return f"rl:{scope}:{digest}"


def hit(scope: str, ident: str, limit: int, window: int) -> bool:
    """Засчитывает одну попытку. True — лимит `limit` за `window` секунд превышен."""
    if not settings.RATELIMIT_ENABLED:
        return False
    limit = limit * settings.RATELIMIT_MULTIPLIER
    key = _key(scope, ident)
    if cache.add(key, 1, timeout=window):
        count = 1
    else:
        try:
            count = cache.incr(key)
        except ValueError:  # ключ истёк между add и incr
            cache.set(key, 1, timeout=window)
            count = 1
    return count > limit


def is_limited(scope: str, ident: str, limit: int) -> bool:
    """Только проверяет счётчик, не засчитывая попытку."""
    if not settings.RATELIMIT_ENABLED:
        return False
    return int(cache.get(_key(scope, ident), 0)) >= limit * settings.RATELIMIT_MULTIPLIER


def honeypot_triggered(request) -> bool:
    return bool(request.POST.get(HONEYPOT_FIELD, "").strip())


def captcha_enabled() -> bool:
    return bool(settings.TURNSTILE_SECRET_KEY)


def verify_captcha(request) -> bool:
    """Проверка токена Turnstile. Без настроенных ключей всегда True.

    Блокируем только тогда, когда Cloudflare прямо ответил, что токен не
    прошёл (или токена нет). Если проблема на нашей стороне — Cloudflare
    недоступен из сети сервера, неверный секретный ключ, внутренняя ошибка —
    капчу пропускаем (fail open) и пишем ошибку в лог: капча существует, чтобы
    помогать, а не ломать регистрацию. Остальные слои (лимиты, honeypot)
    продолжают работать.
    """
    if not captcha_enabled():
        return True
    token = request.POST.get(TURNSTILE_RESPONSE_FIELD, "")
    if not token:
        return False
    payload = urllib.parse.urlencode({
        "secret": settings.TURNSTILE_SECRET_KEY,
        "response": token,
        "remoteip": client_ip(request),
    }).encode()
    try:
        req = urllib.request.Request(TURNSTILE_VERIFY_URL, data=payload)
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.load(resp)
    except urllib.error.HTTPError as exc:
        # Cloudflare отвечает HTTP 400 и на неверный секрет, и (возможно) на плохой
        # токен — решаем по коду ошибки в теле, а не по самому статусу.
        try:
            result = json.load(exc)
        except Exception:
            logger.error("Turnstile siteverify HTTP %s, captcha skipped", exc.code, exc_info=True)
            return True
    except Exception:
        logger.error("Turnstile siteverify unreachable, captcha skipped", exc_info=True)
        return True
    if result.get("success"):
        return True
    error_codes = set(result.get("error-codes") or [])
    if error_codes & TURNSTILE_OUR_SIDE_ERRORS:
        logger.error("Turnstile misconfigured or failing (%s), captcha skipped", sorted(error_codes))
        return True
    return False


def check_public_form(request, scope: str, limit: int, window: int):
    """Общая проверка POST публичной формы: лимит по IP → honeypot → капча.

    Возвращает None, если всё в порядке, иначе причину: "rate", "honeypot"
    или "captcha". Каждая попытка засчитывается в лимит по IP, а каждое
    срабатывание защиты — в штрафы для автоблока (apps/users/blocklist.py).
    """
    ip = client_ip(request)
    if hit(scope, ip, limit, window):
        blocklist.record_strike(ip, scope)
        return "rate"
    if honeypot_triggered(request):
        blocklist.record_strike(ip, f"{scope}:honeypot", HONEYPOT_STRIKES)
        return "honeypot"
    if not verify_captcha(request):
        blocklist.record_strike(ip, f"{scope}:captcha")
        return "captcha"
    return None


MSG_TOO_MANY = "Слишком много попыток. Попробуйте позже."
MSG_CAPTCHA = "Не удалось пройти проверку на робота. Обновите страницу и попробуйте снова."
