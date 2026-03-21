from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Category, Platform


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)


@admin.register(Platform)
class PlatformAdmin(admin.ModelAdmin):
    list_display = (
        "blogger",
        "social_type",
        "url",
        "subscribers",
        "engagement_rate",
        "status",
        "created_at",
    )
    list_filter = ("social_type", "status")
    search_fields = ("blogger__email", "url")
    readonly_fields = ("created_at", "updated_at", "metrics_updated_at")
    filter_horizontal = ("categories",)
    actions = ["approve_platforms", "reject_platforms", "suspend_platforms"]

    @admin.action(description=_("Approve selected platforms"))
    def approve_platforms(self, request, queryset):
        updated = queryset.update(status=Platform.Status.APPROVED, rejection_reason="")
        self.message_user(request, f"{updated} platforms approved.")

    @admin.action(description=_("Reject selected platforms"))
    def reject_platforms(self, request, queryset):
        updated = queryset.update(
            status=Platform.Status.REJECTED,
            rejection_reason="Does not meet platform requirements.",
        )
        self.message_user(request, f"{updated} platforms rejected.")

    @admin.action(description=_("Suspend selected platforms"))
    def suspend_platforms(self, request, queryset):
        updated = queryset.update(status=Platform.Status.SUSPENDED)
        self.message_user(request, f"{updated} platforms suspended.")
