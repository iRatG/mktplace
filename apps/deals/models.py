import uuid as _uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


def _generate_slug():
    return _uuid.uuid4().hex[:16]


class Deal(models.Model):
    class Status(models.TextChoices):
        WAITING_PAYMENT = "waiting_payment", "Waiting Payment"
        IN_PROGRESS = "in_progress", "In Progress"
        ON_APPROVAL = "on_approval", "On Approval"
        WAITING_PUBLICATION = "waiting_publication", "Waiting Publication"
        PUBLISHED = "published", "Published"
        CHECKING = "checking", "Checking"
        COMPLETED = "completed", "Completed"
        DISPUTED = "disputed", "Disputed"
        CANCELLED = "cancelled", "Cancelled"

    campaign = models.ForeignKey(
        "campaigns.Campaign",
        on_delete=models.PROTECT,
        related_name="deals",
    )
    blogger = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="deals_as_blogger",
        limit_choices_to={"role": "blogger"},
    )
    platform = models.ForeignKey(
        "platforms.Platform",
        on_delete=models.PROTECT,
        related_name="deals",
    )
    advertiser = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="deals_as_advertiser",
        limit_choices_to={"role": "advertiser"},
    )
    response = models.OneToOneField(
        "campaigns.Response",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deal",
    )
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(0)]
    )
    status = models.CharField(
        max_length=30, choices=Status.choices, default=Status.WAITING_PAYMENT
    )

    # Creative fields
    creative_text = models.TextField(blank=True)
    creative_media = models.FileField(
        upload_to="deal_creatives/", null=True, blank=True
    )
    creative_submitted_at = models.DateTimeField(null=True, blank=True)
    creative_approved_at = models.DateTimeField(null=True, blank=True)
    creative_rejection_reason = models.TextField(blank=True)

    # Publication fields
    publication_url = models.URLField(blank=True)
    publication_at = models.DateTimeField(null=True, blank=True)

    # Dispute fields
    dispute_reason = models.TextField(blank=True)
    dispute_opened_at = models.DateTimeField(null=True, blank=True)
    dispute_resolved_at = models.DateTimeField(null=True, blank=True)
    dispute_resolution = models.TextField(blank=True)

    # Data retention fields (REQ-5 — Закон «О рекламе» ст.15, 3 года хранения)
    last_distributed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Дата последнего распространения рекламы. От этой даты отсчитывается 3-летний срок хранения материалов.",
    )
    is_frozen = models.BooleanField(
        default=False,
        help_text="Материалы заморожены (активный или завершённый спор) — удаление запрещено до истечения 3 лет.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Deal"
        verbose_name_plural = "Deals"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Deal#{self.pk} {self.blogger.email} / {self.campaign.name} ({self.status})"


class DealStatusLog(models.Model):
    deal = models.ForeignKey(
        Deal,
        on_delete=models.CASCADE,
        related_name="status_logs",
    )
    old_status = models.CharField(max_length=30, blank=True)
    new_status = models.CharField(max_length=30)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deal_status_changes",
    )
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Deal Status Log"
        verbose_name_plural = "Deal Status Logs"
        ordering = ["created_at"]

    def __str__(self):
        return f"Deal#{self.deal_id}: {self.old_status} -> {self.new_status}"

    @classmethod
    def log(cls, deal, new_status, changed_by=None, comment=""):
        cls.objects.create(
            deal=deal,
            old_status=deal.status,
            new_status=new_status,
            changed_by=changed_by,
            comment=comment,
        )


class ChatMessage(models.Model):
    deal = models.ForeignKey(
        Deal,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="sent_deal_messages",
    )
    text = models.TextField(blank=True)
    file = models.FileField(upload_to="deal_chat_files/", null=True, blank=True)
    is_system = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Chat Message"
        verbose_name_plural = "Chat Messages"
        ordering = ["created_at"]

    def __str__(self):
        return f"Message in Deal#{self.deal_id} by {getattr(self.sender, 'email', 'system')}"


class Review(models.Model):
    """Отзыв рекламодателя о блогере после завершения сделки (Модуль 7).

    Создаётся в течение 7 дней после перехода сделки в статус COMPLETED.
    Один отзыв на одну сделку (OneToOne к Deal).

    author — рекламодатель, оставивший отзыв.
    target — блогер, получивший отзыв.
    rating — оценка от 1 до 5.
    text   — произвольный текст отзыва (необязательно).

    После создания пересчитывается BloggerProfile.rating (среднее всех оценок).
    """

    deal = models.OneToOneField(
        Deal,
        on_delete=models.CASCADE,
        related_name="review",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reviews_written",
    )
    target = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reviews_received",
    )
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Review"
        verbose_name_plural = "Reviews"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Review#{self.pk} by {self.author.email} → {self.target.email} ({self.rating}★)"


# ── CPA Tracking (Sprint 8) ──────────────────────────────────────────────────

class TrackingLink(models.Model):
    """Уникальная трекинговая ссылка для CPA-сделки.

    Создаётся лениво при первом открытии страницы сделки (get_or_create).
    Slug — 16-символьный hex UUID, используется в публичном URL /t/<slug>/.
    """

    deal = models.OneToOneField(
        Deal,
        on_delete=models.CASCADE,
        related_name="tracking_link",
    )
    slug = models.CharField(
        max_length=16,
        unique=True,
        db_index=True,
        default=_generate_slug,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Tracking Link"
        verbose_name_plural = "Tracking Links"

    def __str__(self):
        return f"TrackingLink#{self.pk} slug={self.slug} deal={self.deal_id}"

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse("web:cpa_click_track", kwargs={"slug": self.slug})


class ClickLog(models.Model):
    """Один клик по трекинговой ссылке."""

    tracking_link = models.ForeignKey(
        TrackingLink,
        on_delete=models.CASCADE,
        related_name="clicks",
    )
    click_id = models.UUIDField(unique=True, default=_uuid.uuid4, db_index=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Click Log"
        verbose_name_plural = "Click Logs"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Click {self.click_id} on TrackingLink#{self.tracking_link_id}"


class Conversion(models.Model):
    """Конверсия по CPA-сделке.

    Типы:
    - CLICK  — мгновенная оплата при клике (без постбека)
    - LEAD   — лид, подтверждается постбеком
    - SALE   — продажа, подтверждается постбеком
    - INSTALL — установка приложения, подтверждается постбеком

    credited=True означает что BillingService.credit_cpa_conversion уже начислил.
    """

    class ConversionType(models.TextChoices):
        CLICK = "click", "Click"
        LEAD = "lead", "Lead"
        SALE = "sale", "Sale"
        INSTALL = "install", "Install"

    tracking_link = models.ForeignKey(
        TrackingLink,
        on_delete=models.CASCADE,
        related_name="conversions",
    )
    click_log = models.ForeignKey(
        ClickLog,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversions",
    )
    conversion_type = models.CharField(
        max_length=20,
        choices=ConversionType.choices,
        default=ConversionType.CLICK,
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    credited = models.BooleanField(default=False)
    postback_raw = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Conversion"
        verbose_name_plural = "Conversions"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Conversion#{self.pk} type={self.conversion_type} amount={self.amount} credited={self.credited}"
