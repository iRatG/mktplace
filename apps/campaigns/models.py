from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class Campaign(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        MODERATION = "moderation", "Moderation"
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        COMPLETED = "completed", "Completed"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    class PaymentType(models.TextChoices):
        FIXED = "fixed", "Fixed"
        CPA = "cpa", "CPA"

    class CPAType(models.TextChoices):
        CLICK = "click", "Click"
        LEAD = "lead", "Lead"
        SALE = "sale", "Sale"
        INSTALL = "install", "Install"

    advertiser = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="campaigns",
        limit_choices_to={"role": "advertiser"},
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    category = models.ForeignKey(
        "platforms.Category",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="campaigns",
    )
    image = models.ImageField(upload_to="campaign_images/", null=True, blank=True)
    content_types = models.JSONField(
        default=list,
        help_text="List of allowed content types, e.g. ['post', 'stories', 'video', 'review']",
    )
    required_elements = models.JSONField(
        default=dict,
        help_text="Required elements in creative, e.g. {'hashtags': ['#brand'], 'mentions': ['@brand']}",
    )
    payment_type = models.CharField(
        max_length=10, choices=PaymentType.choices, default=PaymentType.FIXED
    )
    fixed_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    cpa_type = models.CharField(
        max_length=20, choices=CPAType.choices, null=True, blank=True
    )
    cpa_rate = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    cpa_tracking_url = models.URLField(blank=True)
    budget = models.DecimalField(
        max_digits=14, decimal_places=2, validators=[MinValueValidator(0)]
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    deadline = models.DateField(
        null=True, blank=True,
        help_text="Deadline for bloggers to submit content",
    )
    min_subscribers = models.PositiveIntegerField(default=0)
    min_er = models.DecimalField(
        max_digits=5, decimal_places=2, default=0.00,
        validators=[MinValueValidator(0)],
    )
    allowed_socials = models.JSONField(
        default=list,
        help_text="List of allowed social platforms, e.g. ['vk', 'telegram']",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    rejection_reason = models.TextField(blank=True)
    max_bloggers = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Campaign"
        verbose_name_plural = "Campaigns"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.status})"


class Response(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"

    blogger = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="campaign_responses",
        limit_choices_to={"role": "blogger"},
    )
    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name="responses",
    )
    platform = models.ForeignKey(
        "platforms.Platform",
        on_delete=models.CASCADE,
        related_name="responses",
    )
    content_type = models.CharField(max_length=50)
    proposed_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    message = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Campaign Response"
        verbose_name_plural = "Campaign Responses"
        unique_together = [("blogger", "campaign", "platform")]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.blogger.email} -> {self.campaign.name} ({self.status})"


class DirectOffer(models.Model):
    """Advertiser initiates a deal directly to a blogger (reverse of Response)."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"

    advertiser = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="direct_offers_sent",
        limit_choices_to={"role": "advertiser"},
    )
    blogger = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="direct_offers_received",
        limit_choices_to={"role": "blogger"},
    )
    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name="direct_offers",
    )
    platform = models.ForeignKey(
        "platforms.Platform",
        on_delete=models.CASCADE,
        related_name="direct_offers",
    )
    content_type = models.CharField(max_length=50)
    proposed_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    message = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    deal = models.OneToOneField(
        "deals.Deal",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="direct_offer",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Direct Offer"
        verbose_name_plural = "Direct Offers"
        unique_together = [("advertiser", "campaign", "platform")]
        ordering = ["-created_at"]

    def __str__(self):
        return f"DirectOffer {self.advertiser.email} → {self.blogger.email} ({self.status})"
