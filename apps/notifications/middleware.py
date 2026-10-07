"""Уведомление гаснет, когда адресат открыл страницу, на которую оно ведёт.

Одно правило для всех типов: views не нужно помнить о гашении. Гасим ДО вызова view — счётчик на колокольчике
считается при отрисовке этой же страницы (context processor), и иначе он отставал бы на одну загрузку
(QA camp_test_3). Учитывается только успешный GET (200): если ответ — редирект, 403 или 404, погашенные
уведомления возвращаются в непрочитанные — страницу пользователь не увидел.
"""
from django.db.models import Q
from django.urls import Resolver404, resolve

from .models import Notification


class NotificationResolveMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        resolved = []
        if request.method == "GET" and user is not None and user.is_authenticated:
            resolved = resolve_for_page(user, request.path, request.get_full_path())
        response = self.get_response(request)
        if resolved and response.status_code != 200:
            Notification.objects.filter(pk__in=resolved).update(is_read=False)
        return response


def _page_target(path, full_path=None):
    """Условие «уведомление ведёт на эту страницу»: сохранённый url, а для страниц сделки и кампании — ещё и связь."""
    target = Q(url__in={path, full_path or path})
    try:
        match = resolve(path)
    except Resolver404:
        return target
    if match.view_name == "web:deal_detail":
        target |= Q(related_deal_id=match.kwargs.get("pk"))
    elif match.view_name == "web:campaign_detail":
        target |= Q(related_campaign_id=match.kwargs.get("pk"))
    return target


def resolve_for_page(user, path, full_path=None):
    """Отмечает прочитанными уведомления пользователя, ведущие на страницу path. Возвращает их id."""
    unread = Notification.objects.filter(_page_target(path, full_path), user=user, is_read=False)
    ids = list(unread.values_list("pk", flat=True))
    if ids:
        Notification.objects.filter(pk__in=ids).update(is_read=True)
    return ids
