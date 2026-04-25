from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_regulated = models.BooleanField(
        default=False,
        help_text="Деятельность требует лицензии/разрешения по Приложению №1 к Закону РУз № ЗРУ-701 от 14.07.2021",
    )
    regulated_doc_hint = models.TextField(
        blank=True,
        help_text="Описание документов, необходимых для данной регулируемой категории",
    )

    class Meta:
        verbose_name = "Category"
        verbose_name_plural = "Categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class PermitDocument(models.Model):
    """Разрешительный документ пользователя для регулируемой категории (REQ-2).

    Хранит лицензию/разрешение/уведомление, статус проверки и срок действия.
    При истечении срока — площадки пользователя в регулируемой категории приостанавливаются.
    """

    class DocType(models.TextChoices):
        LICENSE = "license", "Лицензия"
        PERMIT = "permit", "Разрешение"
        NOTIFICATION = "notification", "Уведомление"
        OTHER = "other", "Иное"

    class Status(models.TextChoices):
        PENDING = "pending", "На проверке"
        APPROVED = "approved", "Подтверждён"
        REJECTED = "rejected", "Отклонён"
        EXPIRED = "expired", "Истёк"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="permit_documents",
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="permit_documents",
        limit_choices_to={"is_regulated": True},
    )
    doc_type = models.CharField(max_length=20, choices=DocType.choices)
    doc_number = models.CharField(max_length=100)
    issued_by = models.CharField(max_length=255, help_text="Орган, выдавший документ")
    issued_date = models.DateField()
    expires_at = models.DateField(
        null=True, blank=True, help_text="Оставьте пустым если документ бессрочный"
    )
    file = models.FileField(upload_to="permits/%Y/%m/", help_text="PDF, JPG или PNG")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    rejection_reason = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_permits",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Разрешительный документ"
        verbose_name_plural = "Разрешительные документы"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.email} — {self.category.name} ({self.get_status_display()})"


class Platform(models.Model):
    class SocialType(models.TextChoices):
        VK = "vk", "VK"
        TELEGRAM = "telegram", "Telegram"
        YOUTUBE = "youtube", "YouTube"
        INSTAGRAM = "instagram", "Instagram"
        TIKTOK = "tiktok", "TikTok"
        ZEN = "zen", "Zen"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        SUSPENDED = "suspended", "Suspended"
        BLOCKED = "blocked", "Blocked"

    blogger = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="platforms",
        limit_choices_to={"role": "blogger"},
    )
    social_type = models.CharField(max_length=20, choices=SocialType.choices)
    url = models.URLField()
    categories = models.ManyToManyField(
        Category, related_name="platforms", blank=True
    )
    subscribers = models.PositiveIntegerField(default=0)
    avg_views = models.PositiveIntegerField(default=0)
    engagement_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=0.00,
        validators=[MinValueValidator(0)],
    )
    price_post = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    price_stories = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    price_video = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    price_review = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    rejection_reason = models.TextField(blank=True)
    metrics_updated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Platform"
        verbose_name_plural = "Platforms"
        unique_together = [("blogger", "social_type", "url")]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.blogger.email} — {self.social_type} ({self.status})"
