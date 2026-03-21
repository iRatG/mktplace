from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import EmailConfirmationToken, PasswordResetToken, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = (
        "email",
        "role",
        "status",
        "is_email_confirmed",
        "is_staff",
        "date_joined",
    )
    list_filter = ("role", "status", "is_email_confirmed", "is_staff")
    search_fields = ("email",)
    ordering = ("-date_joined",)
    readonly_fields = ("date_joined", "last_login", "login_attempts", "blocked_until")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            _("Personal info"),
            {"fields": ("role", "status", "is_email_confirmed")},
        ),
        (
            _("Security"),
            {
                "fields": (
                    "login_attempts",
                    "blocked_until",
                    "email_confirmation_token",
                    "email_confirmation_expires",
                )
            },
        ),
        (
            _("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "role", "password1", "password2"),
            },
        ),
    )

    actions = ["activate_users", "block_users"]

    @admin.action(description=_("Activate selected users"))
    def activate_users(self, request, queryset):
        updated = queryset.update(status=User.Status.ACTIVE, is_email_confirmed=True)
        self.message_user(request, f"{updated} users activated.")

    @admin.action(description=_("Block selected users"))
    def block_users(self, request, queryset):
        updated = queryset.update(status=User.Status.BLOCKED)
        self.message_user(request, f"{updated} users blocked.")


@admin.register(EmailConfirmationToken)
class EmailConfirmationTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "token", "created_at", "expires_at", "is_used")
    list_filter = ("is_used",)
    search_fields = ("user__email",)
    readonly_fields = ("token", "created_at")


@admin.register(PasswordResetToken)
class PasswordResetTokenAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "token",
        "created_at",
        "expires_at",
        "is_used",
        "ip_address",
    )
    list_filter = ("is_used",)
    search_fields = ("user__email", "ip_address")
    readonly_fields = ("token", "created_at")
