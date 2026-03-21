from django.contrib import admin

from .models import AdvertiserProfile, BloggerProfile


@admin.register(AdvertiserProfile)
class AdvertiserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "company_name", "industry", "is_complete", "created_at")
    list_filter = ("is_complete", "industry")
    search_fields = ("user__email", "company_name", "inn")
    readonly_fields = ("created_at", "updated_at")


@admin.register(BloggerProfile)
class BloggerProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "nickname", "rating", "deals_count", "created_at")
    search_fields = ("user__email", "nickname")
    readonly_fields = ("created_at", "updated_at", "rating", "deals_count")
