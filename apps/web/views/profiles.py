from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from apps.deals.models import Deal, Review
from apps.platforms.models import Platform
from apps.profiles.models import AdvertiserProfile, BloggerProfile
from apps.users.models import User

from ..forms import AdvertiserProfileForm, BloggerProfileForm
from .pages import _redirect_dashboard


@login_required
def profile_view(request):
    user = request.user
    if user.is_staff:
        return redirect("web:admin_dashboard")
    if user.role == User.Role.BLOGGER:
        profile, _ = BloggerProfile.objects.get_or_create(user=user)
        platforms = Platform.objects.filter(blogger=user).prefetch_related("categories")
        completed_deals = Deal.objects.filter(blogger=user, status=Deal.Status.COMPLETED).count()
        return render(request, "profiles/my_profile.html", {
            "profile": profile,
            "platforms": platforms,
            "completed_deals": completed_deals,
        })
    else:
        profile, _ = AdvertiserProfile.objects.get_or_create(user=user)
        return render(request, "profiles/my_profile.html", {
            "profile": profile,
        })


@login_required
def profile_edit(request):
    user = request.user
    if user.is_staff:
        return redirect("web:admin_dashboard")
    if user.role == User.Role.BLOGGER:
        profile, _ = BloggerProfile.objects.get_or_create(user=user)
        form = BloggerProfileForm(request.POST or None, instance=profile)
    else:
        profile, _ = AdvertiserProfile.objects.get_or_create(user=user)
        form = AdvertiserProfileForm(request.POST or None, instance=profile)

    if request.method == "POST" and form.is_valid():
        saved = form.save()
        saved.check_completeness()
        messages.success(request, "Профиль обновлён.")
        return redirect("web:profile")

    return render(request, "profiles/edit_profile.html", {"form": form})


@login_required
def blogger_public_profile(request, pk):
    """Публичный профиль блогера — виден авторизованным пользователям (Модули 3, 7, 10).

    Включает: площадки, метрики, число завершённых сделок,
    последние 10 отзывов с рейтингом (Модуль 7).

    Контекст шаблона:
        blogger         — User (role=BLOGGER)
        profile         — BloggerProfile
        platforms       — одобренные площадки с категориями
        completed_deals — число завершённых сделок
        reviews         — последние 10 отзывов (Review QuerySet)
    """
    blogger = get_object_or_404(User, pk=pk, role=User.Role.BLOGGER)
    profile, _ = BloggerProfile.objects.get_or_create(user=blogger)
    platforms = Platform.objects.filter(
        blogger=blogger, status=Platform.Status.APPROVED
    ).prefetch_related("categories")
    completed_deals = Deal.objects.filter(
        blogger=blogger, status=Deal.Status.COMPLETED
    ).count()
    reviews = Review.objects.filter(target=blogger).select_related("author")[:10]
    return render(request, "profiles/blogger_public.html", {
        "blogger": blogger,
        "profile": profile,
        "platforms": platforms,
        "completed_deals": completed_deals,
        "reviews": reviews,
    })
