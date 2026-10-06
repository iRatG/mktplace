"""Уведомление гаснет, когда адресат открыл страницу, на которую оно ведёт.

Одно правило для всех типов: views не нужно помнить о гашении. Учитывается только успешный
GET (200) — редирект, 403 и 404 значат, что страницу пользователь не увидел.
"""
from django.db.models import Q
from django.urls import Resolver404, resolve

from .models import Notification


class NotificationResolveMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, "user", None)
        if (
            request.method == "GET"
            and response.status_code == 200
            and user is not None
            and user.is_authenticated
        ):
            resolve_for_page(user, request.path, request.get_full_path())
        return response


def resolve_for_page(user, path, full_path=None):
    """Отмечает прочитанными уведомления пользователя, ведущие на страницу path."""
    target = Q(url__in={path, full_path or path})
    try:
        match = resolve(path)
    except Resolver404:
        match = None
    if match is not None and match.view_name == "web:deal_detail":
        target |= Q(url="", related_deal_id=match.kwargs.get("pk"))
    Notification.objects.filter(target, user=user, is_read=False).update(is_read=True)
