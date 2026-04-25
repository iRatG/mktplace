from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.platforms.models import PermitDocument, Platform
from apps.users.models import User

from ..forms import PlatformForm
from .pages import _redirect_dashboard


def _check_regulated_permit(user, categories_qs):
    """Вернуть список регулируемых категорий, для которых нет одобренного документа."""
    regulated = list(categories_qs.filter(is_regulated=True))
    missing = []
    for cat in regulated:
        has_approved = PermitDocument.objects.filter(
            user=user, category=cat, status=PermitDocument.Status.APPROVED
        ).exists()
        if not has_approved:
            missing.append(cat)
    return missing


@login_required
def platform_add(request):
    if request.user.role != User.Role.BLOGGER:
        return _redirect_dashboard(request.user)
    form = PlatformForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        selected_categories = form.cleaned_data.get("categories")
        missing = _check_regulated_permit(request.user, selected_categories) if selected_categories else []
        if missing:
            names = ", ".join(cat.name for cat in missing)
            messages.error(
                request,
                f"Для категорий «{names}» требуется разрешительный документ (лицензия/разрешение). "
                f"Загрузите документ в разделе «Разрешительные документы» и дождитесь проверки.",
            )
            return render(request, "platforms/platform_form.html", {
                "form": form,
                "editing": False,
                "missing_permits": missing,
            })
        platform = form.save(commit=False)
        platform.blogger = request.user
        platform.save()
        form.save_m2m()
        messages.success(request, "Площадка добавлена и отправлена на модерацию.")
        return redirect("web:profile")
    return render(request, "platforms/platform_form.html", {"form": form, "editing": False})


@login_required
def platform_edit(request, pk):
    platform = get_object_or_404(Platform, pk=pk, blogger=request.user)
    form = PlatformForm(request.POST or None, instance=platform)
    if request.method == "POST" and form.is_valid():
        selected_categories = form.cleaned_data.get("categories")
        missing = _check_regulated_permit(request.user, selected_categories) if selected_categories else []
        if missing:
            names = ", ".join(cat.name for cat in missing)
            messages.error(
                request,
                f"Для категорий «{names}» требуется разрешительный документ. "
                f"Загрузите документ в разделе «Разрешительные документы».",
            )
            return render(request, "platforms/platform_form.html", {
                "form": form,
                "editing": True,
                "platform": platform,
                "missing_permits": missing,
            })
        updated = form.save(commit=False)
        # If URL changed on an approved platform — send back to moderation
        url_changed = "url" in form.changed_data
        if url_changed and platform.status == Platform.Status.APPROVED:
            updated.status = Platform.Status.PENDING
            updated.rejection_reason = ""
            messages.warning(request, "URL изменён — площадка отправлена на повторную модерацию.")
        else:
            messages.success(request, "Площадка обновлена.")
        updated.save()
        form.save_m2m()
        return redirect("web:profile")
    return render(request, "platforms/platform_form.html", {"form": form, "editing": True, "platform": platform})


@login_required
@require_POST
def platform_delete(request, pk):
    platform = get_object_or_404(Platform, pk=pk, blogger=request.user)
    if platform.status in (Platform.Status.PENDING, Platform.Status.REJECTED):
        platform.delete()
        messages.success(request, "Площадка удалена.")
    else:
        messages.error(request, "Нельзя удалить одобренную площадку.")
    return redirect("web:profile")
