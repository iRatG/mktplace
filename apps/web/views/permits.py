"""Views для работы с разрешительными документами (REQ-2).

Пользователь:
    /profile/permits/              — список своих документов
    /profile/permits/upload/       — загрузить новый документ
    /profile/permits/<pk>/delete/  — удалить PENDING или REJECTED документ

Администратор (is_staff):
    /panel/permits/                — список документов на проверке
    /panel/permits/<pk>/approve/   — подтвердить документ
    /panel/permits/<pk>/reject/    — отклонить с указанием причины
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.platforms.models import PermitDocument
from ..forms import AdminPermitRejectForm, PermitDocumentForm
from .admin_panel import _staff_required


@login_required
def permit_list(request):
    """Список разрешительных документов текущего пользователя."""
    permits = PermitDocument.objects.filter(user=request.user).select_related("category")
    return render(request, "permits/list.html", {"permits": permits})


@login_required
def permit_upload(request):
    """Форма загрузки нового разрешительного документа."""
    form = PermitDocumentForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        permit = form.save(commit=False)
        permit.user = request.user
        permit.save()
        messages.success(request, "Документ загружен и отправлен на проверку.")
        return redirect("web:permit_list")
    return render(request, "permits/upload.html", {"form": form})


@login_required
@require_POST
def permit_delete(request, pk):
    """Удалить документ (только PENDING или REJECTED)."""
    permit = get_object_or_404(PermitDocument, pk=pk, user=request.user)
    if permit.status in (PermitDocument.Status.PENDING, PermitDocument.Status.REJECTED):
        permit.file.delete(save=False)
        permit.delete()
        messages.success(request, "Документ удалён.")
    else:
        messages.error(request, "Нельзя удалить одобренный или истёкший документ.")
    return redirect("web:permit_list")


@_staff_required
def admin_permits(request):
    """Список разрешительных документов на проверке (PENDING)."""
    permits = (
        PermitDocument.objects.filter(status=PermitDocument.Status.PENDING)
        .select_related("user", "category")
        .order_by("created_at")
    )
    return render(request, "admin_panel/permits.html", {"permits": permits})


@_staff_required
@require_POST
def admin_permit_approve(request, pk):
    """Подтвердить разрешительный документ."""
    from django.utils import timezone
    permit = get_object_or_404(PermitDocument, pk=pk, status=PermitDocument.Status.PENDING)
    permit.status = PermitDocument.Status.APPROVED
    permit.reviewed_by = request.user
    permit.reviewed_at = timezone.now()
    permit.rejection_reason = ""
    permit.save(update_fields=["status", "reviewed_by", "reviewed_at", "rejection_reason", "updated_at"])
    messages.success(request, f"Документ {permit.doc_number} подтверждён.")
    return redirect("web:admin_permits")


@_staff_required
@require_POST
def admin_permit_reject(request, pk):
    """Отклонить разрешительный документ с указанием причины."""
    from django.utils import timezone
    permit = get_object_or_404(PermitDocument, pk=pk, status=PermitDocument.Status.PENDING)
    form = AdminPermitRejectForm(request.POST)
    if form.is_valid():
        permit.status = PermitDocument.Status.REJECTED
        permit.reviewed_by = request.user
        permit.reviewed_at = timezone.now()
        permit.rejection_reason = form.cleaned_data["rejection_reason"]
        permit.save(update_fields=["status", "reviewed_by", "reviewed_at", "rejection_reason", "updated_at"])
        messages.success(request, f"Документ {permit.doc_number} отклонён.")
    else:
        messages.error(request, "Укажите причину отклонения.")
    return redirect("web:admin_permits")
