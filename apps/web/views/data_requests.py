"""«Мои данные» и очередь обращений по персональным данным."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.billing.models import PayoutRequisites
from apps.deals.models import Deal
from apps.platforms.models import Platform
from apps.registration.models import IdentityVerification, IPApplication
from apps.users import data_requests
from apps.users.data_requests import DataRequestError
from apps.users.models import DataRequest, User
from apps.users.pd_access import log_access

from ..forms import DataRequestForm
from .admin_panel import _staff_required
from .pages import _redirect_dashboard


def _held_data(user):
    """Какие данные о пользователе хранятся: категории и объём."""
    rows = [("Учётная запись", user.login_display)]
    if user.role == User.Role.BLOGGER:
        rows += [
            ("Профиль блогера", user.blogger_profile.nickname or "заполняется вами"),
            ("Площадки", Platform.objects.filter(blogger=user).count()),
            ("Подтверждение личности", IdentityVerification.objects.filter(user=user).count()),
            ("Документы статуса", IPApplication.objects.filter(user=user).count()),
            ("Реквизиты выплаты", PayoutRequisites.objects.filter(user=user).count()),
            ("Сделки", Deal.objects.filter(blogger=user).count()),
        ]
    else:
        rows += [
            ("Профиль компании", user.advertiser_profile.company_name or "заполняется вами"),
            ("Сделки", Deal.objects.filter(advertiser=user).count()),
        ]
    return rows


@login_required
def my_data(request):
    user = request.user
    if user.is_staff:
        return _redirect_dashboard(user)
    form = DataRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            data_requests.create_request(user, form.cleaned_data["kind"], form.cleaned_data["text"])
            messages.success(request, "Обращение зарегистрировано.")
            return redirect("web:my_data")
        except DataRequestError as e:
            form.add_error(None, str(e))
    return render(request, "profiles/my_data.html", {
        "form": form,
        "held": _held_data(user),
        "requests": user.data_requests.all(),
        "consents": user.consents.all()[:20],
        "blockers": data_requests.closure_blockers(user),
        "kept": data_requests.KEPT_AFTER_CLOSING,
    })


@_staff_required
def admin_data_requests(request):
    queue = list(DataRequest.objects.filter(status=DataRequest.Status.OPEN).select_related("user").order_by("due_at"))
    log_access(request, f"Очередь обращений по персональным данным: {len(queue)}")
    return render(request, "admin_panel/data_requests.html", {"queue": queue})


@_staff_required
def admin_data_request_detail(request, pk):
    item = get_object_or_404(DataRequest.objects.select_related("user", "answered_by"), pk=pk)
    log_access(request, f"Обращение по персональным данным №{item.pk}", subject=item.user)
    closing = item.kind == DataRequest.Kind.CLOSE and item.is_open
    return render(request, "admin_panel/data_request_detail.html", {
        "item": item,
        "held": _held_data(item.user) if item.is_open else [],
        "blockers": data_requests.closure_blockers(item.user) if closing else [],
        "closing": closing,
        "kept": data_requests.KEPT_AFTER_CLOSING,
    })


@_staff_required
@require_POST
def admin_data_request_answer(request, pk):
    status = DataRequest.Status.DONE if request.POST.get("action") == "done" else DataRequest.Status.REJECTED
    try:
        data_requests.answer_request(pk, request.user, status, request.POST.get("answer"))
        messages.success(request, "Ответ отправлен пользователю.")
        return redirect("web:admin_data_requests")
    except DataRequestError as e:
        messages.error(request, str(e))
    return redirect("web:admin_data_request_detail", pk=pk)


@_staff_required
@require_POST
def admin_data_request_close_account(request, pk):
    if request.POST.get("confirm") != "on":
        messages.error(request, "Отметьте, что проверили обращение: закрытие нельзя отменить.")
        return redirect("web:admin_data_request_detail", pk=pk)
    try:
        item = data_requests.close_account(pk, request.user)
        log_access(request, f"Закрытие аккаунта по обращению №{item.pk}", subject=item.user)
        messages.success(request, "Аккаунт закрыт, данные удалены.")
        return redirect("web:admin_data_requests")
    except DataRequestError as e:
        messages.error(request, str(e))
    return redirect("web:admin_data_request_detail", pk=pk)
