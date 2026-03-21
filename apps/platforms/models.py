from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        verbose_name = "Category"
        verbose_name_plural = "Categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


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
