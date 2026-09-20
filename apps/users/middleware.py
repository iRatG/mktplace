from django.http import HttpResponse

from . import blocklist
from .security import client_ip

BLOCKED_MESSAGE = (
    "Доступ с вашего адреса ограничен. "
    "Если вы считаете, что это ошибка, напишите в поддержку ublogers."
)


class BlockedIPMiddleware:
    """Отвечает 403 на любые запросы с заблокированных IP (см. apps/users/blocklist.py).

    Авторизованный сотрудник (is_staff) не блокируется никогда: так админ не
    запрёт сам себя и сможет снять блокировку.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if blocklist.is_blocked(client_ip(request)):
            user = getattr(request, "user", None)
            if not (user is not None and user.is_authenticated and user.is_staff):
                return HttpResponse(BLOCKED_MESSAGE, status=403, content_type="text/plain; charset=utf-8")
        return self.get_response(request)
