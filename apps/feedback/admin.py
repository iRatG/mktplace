from django.contrib import admin

from .models import PlatformReview


@admin.register(PlatformReview)
class PlatformReviewAdmin(admin.ModelAdmin):
    list_display = ("id", "author", "rating", "status", "is_anonymous", "created_at")
    list_filter = ("status", "rating")
    readonly_fields = ("author", "rating", "text", "reason", "is_anonymous", "created_at")
