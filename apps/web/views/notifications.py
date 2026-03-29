from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST


@login_required
def notification_list(request):
    """Страница уведомлений пользователя (Модуль 11).

    Показывает последние 50 уведомлений: непрочитанные сверху, затем прочитанные.
    Помечает все уведомления как прочитанные при открытии страницы.

    Доступ: любой авторизованный пользователь.

    Контекст шаблона:
        notifications — QuerySet[Notification] последних 50 записей
        unread_count  — int, число непрочитанных ДО открытия страницы
    """
    from django.core.paginator import Paginator
    from apps.notifications.models import Notification
    qs = Notification.objects.filter(user=request.user).select_related("related_deal").order_by("is_read", "-created_at")
    unread_count = qs.filter(is_read=False).count()
    page_obj = Paginator(qs, 20).get_page(request.GET.get("page", 1))
    # Помечаем все как прочитанные
    qs.filter(is_read=False).update(is_read=True)
    return render(request, "notifications/list.html", {
        "notifications": page_obj,
        "page_obj": page_obj,
        "unread_count": unread_count,
    })


@login_required
@require_POST
def notification_mark_all_read(request):
    """Отметить все уведомления как прочитанные (Модуль 11).

    POST-only. После выполнения: redirect → notification_list.
    """
    from apps.notifications.models import Notification
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    messages.success(request, "Все уведомления отмечены как прочитанные.")
    return redirect("web:notifications")
