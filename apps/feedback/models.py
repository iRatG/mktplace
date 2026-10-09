from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

# Оценка, ниже которой отзыв считается отрицательным — тогда причина недовольства обязательна.
NEGATIVE_RATING_MAX = 2


class PlatformReview(models.Model):
    """Отзыв пользователя о платформе. Публикуется после проверки сотрудником; можно анонимно."""

    class Status(models.TextChoices):
        PENDING = "pending", "На проверке"
        PUBLISHED = "published", "Опубликован"
        REJECTED = "rejected", "Не опубликован"
        REMOVED = "removed", "Удалён"

    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="platform_reviews")
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    text = models.TextField(help_text="Личный опыт использования услуг платформы")
    reason = models.TextField(blank=True, help_text="Причина недовольства — обязательна для отрицательного отзыва")
    is_anonymous = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    moderation_comment = models.TextField(blank=True)
    moderated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    moderated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"PlatformReview#{self.pk} {self.rating}★ ({self.status})"

    @property
    def is_negative(self):
        return self.rating <= NEGATIVE_RATING_MAX

    @property
    def author_label(self):
        return "Аноним" if self.is_anonymous else self.author.public_name
