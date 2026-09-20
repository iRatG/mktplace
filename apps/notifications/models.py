from django.conf import settings
from django.db import models
from django.urls import reverse


class Notification(models.Model):
    class Type(models.TextChoices):
        DEAL_CREATED = "deal_created", "Deal Created"
        DEAL_UPDATED = "deal_updated", "Deal Updated"
        DEAL_COMPLETED = "deal_completed", "Deal Completed"
        DEAL_CANCELLED = "deal_cancelled", "Deal Cancelled"
        DEAL_DISPUTED = "deal_disputed", "Deal Disputed"
        CREATIVE_SUBMITTED = "creative_submitted", "Creative Submitted"
        CREATIVE_APPROVED = "creative_approved", "Creative Approved"
        CREATIVE_REJECTED = "creative_rejected", "Creative Rejected"
        PAYMENT_RECEIVED = "payment_received", "Payment Received"
        WITHDRAWAL_APPROVED = "withdrawal_approved", "Withdrawal Approved"
        WITHDRAWAL_REJECTED = "withdrawal_rejected", "Withdrawal Rejected"
        CAMPAIGN_RESPONSE = "campaign_response", "Campaign Response"
        CAMPAIGN_STATUS = "campaign_status", "Campaign Status Changed"
        PLATFORM_MODERATED = "platform_moderated", "Platform Moderated"
        RESPONSE_ACCEPTED = "response_accepted", "Response Accepted"
        RESPONSE_REJECTED = "response_rejected", "Response Rejected"
        DIRECT_OFFER_RECEIVED = "direct_offer_received", "Direct Offer Received"
        DIRECT_OFFER_ACCEPTED = "direct_offer_accepted", "Direct Offer Accepted"
        DIRECT_OFFER_REJECTED = "direct_offer_rejected", "Direct Offer Rejected"
        LEGAL_ENTITY_ASSIGNED = "legal_entity_assigned", "Legal Entity Assigned"
        LEGAL_ENTITY_APPROVED = "legal_entity_approved", "Legal Entity Approved"
        LEGAL_ENTITY_REJECTED = "legal_entity_rejected", "Legal Entity Rejected"
        IP_APPLICATION_APPROVED = "ip_application_approved", "IP Application Approved"
        IP_APPLICATION_REJECTED = "ip_application_rejected", "IP Application Rejected"
        SYSTEM = "system", "System"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    type = models.CharField(max_length=40, choices=Type.choices)
    title = models.CharField(max_length=255)
    body = models.TextField()
    is_read = models.BooleanField(default=False)
    related_deal = models.ForeignKey(
        "deals.Deal",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
    )
    url = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Относительный адрес страницы, где нужно действие по этому уведомлению",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # Запасной адрес для уведомлений без сохранённого url (созданных до появления поля).
    FALLBACK_URL_NAMES = {
        Type.LEGAL_ENTITY_ASSIGNED: "web:admin_legal_entities",
        Type.IP_APPLICATION_APPROVED: "web:ip_application_list",
        Type.IP_APPLICATION_REJECTED: "web:ip_application_list",
        Type.WITHDRAWAL_APPROVED: "web:wallet",
        Type.WITHDRAWAL_REJECTED: "web:wallet",
        Type.PAYMENT_RECEIVED: "web:wallet",
    }

    class Meta:
        verbose_name = "Notification"
        verbose_name_plural = "Notifications"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Notification({self.type}) for {self.user.email}"

    @property
    def target_url(self):
        """Куда вести по клику: сохранённый url → сделка → запасной адрес по типу → ''."""
        if self.url:
            return self.url
        if self.related_deal_id:
            return reverse("web:deal_detail", kwargs={"pk": self.related_deal_id})
        name = self.FALLBACK_URL_NAMES.get(self.type)
        return reverse(name) if name else ""

    def mark_read(self):
        if not self.is_read:
            self.is_read = True
            self.save(update_fields=["is_read"])


class NotificationSettings(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_settings",
    )
    preferences = models.JSONField(
        default=dict,
        help_text=(
            "Notification preferences, e.g. "
            "{'email': True, 'push': False, 'deal_created': True, ...}"
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Notification Settings"
        verbose_name_plural = "Notification Settings"

    def __str__(self):
        return f"NotificationSettings for {self.user.email}"

    def is_enabled(self, notification_type: str, channel: str = "email") -> bool:
        """Check if a specific notification type and channel is enabled."""
        defaults = {"email": True, "push": True}
        channel_enabled = self.preferences.get(channel, defaults.get(channel, True))
        type_enabled = self.preferences.get(notification_type, True)
        return bool(channel_enabled and type_enabled)
