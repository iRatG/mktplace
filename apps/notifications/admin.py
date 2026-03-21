from django.contrib import admin

from .models import Notification, NotificationSettings


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "type", "title", "is_read", "related_deal", "created_at")
    list_filter = ("type", "is_read")
    search_fields = ("user__email", "title")
    readonly_fields = ("created_at",)
    actions = ["mark_as_read"]

    @admin.action(description="Mark selected notifications as read")
    def mark_as_read(self, request, queryset):
        updated = queryset.update(is_read=True)
        self.message_user(request, f"{updated} notifications marked as read.")


@admin.register(NotificationSettings)
class NotificationSettingsAdmin(admin.ModelAdmin):
    list_display = ("user", "updated_at")
    search_fields = ("user__email",)
    readonly_fields = ("created_at", "updated_at")
