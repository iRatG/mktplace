from django.contrib import admin

from .models import ChatMessage, Deal, DealStatusLog


class DealStatusLogInline(admin.TabularInline):
    model = DealStatusLog
    extra = 0
    readonly_fields = ("old_status", "new_status", "changed_by", "comment", "created_at")
    can_delete = False


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    readonly_fields = ("sender", "text", "file", "is_system", "created_at")
    can_delete = False


@admin.register(Deal)
class DealAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "campaign",
        "blogger",
        "advertiser",
        "amount",
        "status",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = ("blogger__email", "advertiser__email", "campaign__name")
    readonly_fields = (
        "created_at",
        "updated_at",
        "creative_submitted_at",
        "creative_approved_at",
        "publication_at",
        "dispute_opened_at",
        "dispute_resolved_at",
    )
    inlines = [DealStatusLogInline, ChatMessageInline]


@admin.register(DealStatusLog)
class DealStatusLogAdmin(admin.ModelAdmin):
    list_display = ("deal", "old_status", "new_status", "changed_by", "created_at")
    list_filter = ("new_status",)
    search_fields = ("deal__id", "changed_by__email")
    readonly_fields = ("created_at",)


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("deal", "sender", "is_system", "created_at")
    list_filter = ("is_system",)
    search_fields = ("deal__id", "sender__email")
    readonly_fields = ("created_at",)
