from django.contrib import admin

from .models import (
    IdentityVerification,
    IPApplication,
    IPApplicationStatusLog,
    LegalEntityApplication,
    LegalEntityApplicationStatusLog,
)


class LegalEntityApplicationStatusLogInline(admin.TabularInline):
    model = LegalEntityApplicationStatusLog
    extra = 0
    readonly_fields = ("old_status", "new_status", "changed_by", "comment", "created_at")
    can_delete = False


@admin.register(LegalEntityApplication)
class LegalEntityApplicationAdmin(admin.ModelAdmin):
    list_display = (
        "company_name",
        "inn",
        "user",
        "status",
        "assigned_to",
        "ddocs_status",
        "created_at",
    )
    list_filter = ("status", "ddocs_status")
    search_fields = ("company_name", "inn", "user__email")
    readonly_fields = ("created_at", "updated_at", "reviewed_at")
    inlines = [LegalEntityApplicationStatusLogInline]


@admin.register(IdentityVerification)
class IdentityVerificationAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "pinfl", "status", "user", "created_at")
    list_filter = ("status",)
    search_fields = ("full_name", "phone", "pinfl", "user__email")
    readonly_fields = ("created_at", "verified_at")


class IPApplicationStatusLogInline(admin.TabularInline):
    model = IPApplicationStatusLog
    extra = 0
    readonly_fields = ("old_status", "new_status", "changed_by", "comment", "created_at")
    can_delete = False


@admin.register(IPApplication)
class IPApplicationAdmin(admin.ModelAdmin):
    list_display = ("user", "document_type", "status", "reviewed_by", "created_at")
    list_filter = ("status", "document_type")
    search_fields = ("user__email", "document_number")
    readonly_fields = ("created_at", "updated_at", "reviewed_at")
    inlines = [IPApplicationStatusLogInline]
