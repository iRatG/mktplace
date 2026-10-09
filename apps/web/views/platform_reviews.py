"""Отзывы о платформе: публичная страница с формой и очередь проверки сотрудником."""
from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.feedback.models import PlatformReview
from apps.feedback.services import ReviewError, moderate_review, remove_review, submit_review

from .admin_panel import _staff_required


def platform_reviews(request):
    """Опубликованные отзывы; вошедший пользователь может оставить свой (публикуется после проверки)."""
    if request.method == "POST":
        if not request.user.is_authenticated:
            return redirect("web:login")
        post = request.POST
        try:
            submit_review(request.user, post.get("rating"), post.get("text"), post.get("reason", ""),
                          post.get("is_anonymous") == "on")
        except ReviewError as e:
            messages.error(request, str(e))
            return render(request, "feedback/list.html", _context(request, form=post))
        messages.success(request, "Спасибо! Отзыв появится после проверки.")
        return redirect("web:platform_reviews")
    return render(request, "feedback/list.html", _context(request))


def _context(request, form=None):
    reviews = PlatformReview.objects.filter(status=PlatformReview.Status.PUBLISHED).select_related(
        "author__blogger_profile", "author__advertiser_profile")
    page_obj = Paginator(reviews, 20).get_page(request.GET.get("page", 1))
    return {"reviews": page_obj, "page_obj": page_obj, "form": form or {}}


@_staff_required
def admin_platform_reviews(request):
    return render(request, "admin_panel/platform_reviews.html", {
        "pending": PlatformReview.objects.filter(status=PlatformReview.Status.PENDING).select_related("author"),
        "published": PlatformReview.objects.filter(status=PlatformReview.Status.PUBLISHED).select_related("author")[:50],
    })


@_staff_required
@require_POST
def admin_platform_review_moderate(request, pk):
    publish = request.POST.get("decision") == "publish"
    try:
        moderate_review(pk, request.user, publish, request.POST.get("comment", ""))
    except ReviewError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, "Отзыв опубликован." if publish else "Отзыв не опубликован, автор уведомлён.")
    return redirect("web:admin_platform_reviews")


@_staff_required
@require_POST
def admin_platform_review_remove(request, pk):
    try:
        remove_review(pk, request.user, request.POST.get("comment", ""))
    except ReviewError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, "Отзыв удалён.")
    return redirect("web:admin_platform_reviews")
