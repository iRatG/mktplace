from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.platforms.models import Platform
from apps.users.models import User

from ..forms import PlatformForm
from .pages import _redirect_dashboard


@login_required
def platform_add(request):
    if request.user.role != User.Role.BLOGGER:
        return _redirect_dashboard(request.user)
    form = PlatformForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
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
