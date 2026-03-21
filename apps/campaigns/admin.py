from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Campaign, Response


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "advertiser",
        "category",
        "payment_type",
        "budget",
        "status",
        "created_at",
    )
    list_filter = ("status", "payment_type")
    search_fields = ("name", "advertiser__email")
    readonly_fields = ("created_at", "updated_at")
    actions = ["approve_campaigns", "reject_campaigns"]

    @admin.action(description=_("Approve selected campaigns (set active)"))
    def approve_campaigns(self, request, queryset):
        updated = queryset.filter(status=Campaign.Status.MODERATION).update(
            status=Campaign.Status.ACTIVE, rejection_reason=""
        )
        self.message_user(request, f"{updated} campaigns activated.")

    @admin.action(description=_("Reject selected campaigns"))
    def reject_campaigns(self, request, queryset):
        updated = queryset.filter(status=Campaign.Status.MODERATION).update(
            status=Campaign.Status.REJECTED,
            rejection_reason="Does not meet campaign requirements.",
        )
        self.message_user(request, f"{updated} campaigns rejected.")


@admin.register(Response)
class ResponseAdmin(admin.ModelAdmin):
    list_display = (
        "blogger",
        "campaign",
        "platform",
        "content_type",
        "proposed_price",
        "status",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = ("blogger__email", "campaign__name")
    readonly_fields = ("created_at", "updated_at")
