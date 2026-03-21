from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


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
