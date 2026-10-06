from django.contrib import admin

from .models import Campaign, CampaignEditProposal, Response


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
    # Модерация и смена статуса — только через панель (/panel/campaigns/): там снимок одобренной
    # версии, проверка правок модератора и срока, уведомления. Здесь статус только для чтения.
    readonly_fields = ("status", "approved_snapshot", "created_at", "updated_at")


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
    readonly_fields = ("status", "created_at", "updated_at")


@admin.register(CampaignEditProposal)
class CampaignEditProposalAdmin(admin.ModelAdmin):
    list_display = ("campaign", "author", "status", "created_at", "responded_at")
    list_filter = ("status",)
    raw_id_fields = ("campaign", "author")
    readonly_fields = ("status",)
