"""Отзывы о платформе: подача пользователем, проверка и публикация сотрудником, удаление."""
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.notifications.models import Notification
from apps.notifications.service import NotificationService

from .models import PlatformReview


class ReviewError(Exception):
    """Отзыв нельзя подать или обработать — текст понятен пользователю."""


def submit_review(author, rating, text, reason="", is_anonymous=False):
    text = (text or "").strip()
    reason = (reason or "").strip()
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        raise ReviewError("Поставьте оценку от 1 до 5.")
    if not 1 <= rating <= 5:
        raise ReviewError("Поставьте оценку от 1 до 5.")
    if len(text) < 20:
        raise ReviewError("Опишите свой опыт использования платформы — хотя бы пару предложений.")
    review = PlatformReview(author=author, rating=rating, text=text, reason=reason, is_anonymous=bool(is_anonymous))
    if review.is_negative and not reason:
        raise ReviewError("В отрицательном отзыве укажите, чем вы остались недовольны.")
    if PlatformReview.objects.filter(author=author, text__iexact=text).exclude(status=PlatformReview.Status.REMOVED).exists():
        raise ReviewError("Такой отзыв вы уже оставляли.")
    review.save()
    from apps.users.models import User

    for staff in User.objects.filter(is_staff=True, is_active=True):
        NotificationService.notify(
            user=staff, notification_type=Notification.Type.SYSTEM, title="Новый отзыв о платформе",
            body=f"Отзыв {rating}★ ждёт проверки.", url=reverse("web:admin_platform_reviews"),
        )
    return review


def moderate_review(pk, staff, publish, comment=""):
    """Сотрудник публикует отзыв или отказывает в публикации (с причиной)."""
    if staff is None or not staff.is_staff:
        raise ReviewError("Проверяет отзывы только сотрудник.")
    comment = (comment or "").strip()
    if not publish and not comment:
        raise ReviewError("Укажите, какое требование к отзыву нарушено.")
    with transaction.atomic():
        review = PlatformReview.objects.select_for_update().filter(pk=pk).first()
        if review is None or review.status != PlatformReview.Status.PENDING:
            raise ReviewError("Отзыв уже обработан.")
        review.status = PlatformReview.Status.PUBLISHED if publish else PlatformReview.Status.REJECTED
        review.moderation_comment = comment
        review.moderated_by = staff
        review.moderated_at = timezone.now()
        review.save(update_fields=["status", "moderation_comment", "moderated_by", "moderated_at"])
    NotificationService.notify(
        user=review.author, notification_type=Notification.Type.SYSTEM,
        title="Отзыв опубликован" if publish else "Отзыв не опубликован",
        body=("Спасибо! Ваш отзыв о платформе опубликован." if publish
              else f"Ваш отзыв о платформе не опубликован: {comment}"),
        url=reverse("web:platform_reviews"),
    )
    return review


def remove_review(pk, staff, comment):
    """Удалить опубликованный отзыв, нарушающий требования или права третьих лиц."""
    if staff is None or not staff.is_staff:
        raise ReviewError("Удаляет отзывы только сотрудник.")
    comment = (comment or "").strip()
    if not comment:
        raise ReviewError("Укажите причину удаления.")
    updated = PlatformReview.objects.filter(pk=pk, status=PlatformReview.Status.PUBLISHED).update(
        status=PlatformReview.Status.REMOVED, moderation_comment=comment, moderated_by=staff,
        moderated_at=timezone.now(),
    )
    if not updated:
        raise ReviewError("Опубликованный отзыв не найден.")
