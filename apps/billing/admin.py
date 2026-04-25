from django.contrib import admin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import TestBalanceGrant, Transaction, Wallet, WithdrawalRequest
from .services import BillingService


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("user", "is_demo_badge", "available_balance", "reserved_balance", "on_withdrawal", "updated_at")
    search_fields = ("user__email",)
    readonly_fields = ("created_at", "updated_at")
    actions = ["grant_test_balance_action"]

    @admin.display(description="Demo", boolean=True)
    def is_demo_badge(self, obj):
        return obj.user.is_demo

    @admin.action(description=_("Grant test balance to selected demo accounts"))
    def grant_test_balance_action(self, request, queryset):
        success = 0
        skipped = 0
        errors = []
        for wallet in queryset.select_related("user"):
            if not wallet.user.is_demo:
                skipped += 1
                continue
            try:
                BillingService.grant_test_balance(
                    user=wallet.user,
                    amount=50_000,
                    granted_by=request.user,
                    note="Admin bulk grant via admin panel",
                )
                success += 1
            except ValueError as e:
                errors.append(f"{wallet.user.email}: {e}")

        if success:
            self.message_user(request, f"Test balance granted to {success} demo account(s).")
        if skipped:
            self.message_user(request, f"{skipped} account(s) skipped (not demo).", level="warning")
        for err in errors:
            self.message_user(request, err, level="error")


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("wallet", "type", "amount", "balance_after", "deal", "created_at")
    list_filter = ("type",)
    search_fields = ("wallet__user__email",)
    readonly_fields = ("created_at",)


@admin.register(WithdrawalRequest)
class WithdrawalRequestAdmin(admin.ModelAdmin):
    list_display = ("blogger", "amount", "status", "created_at", "processed_at")
    list_filter = ("status",)
    search_fields = ("blogger__email",)
    readonly_fields = ("created_at", "updated_at", "processed_at")
    actions = ["approve_withdrawals", "reject_withdrawals", "complete_withdrawals"]

    @admin.action(description=_("Approve selected withdrawal requests"))
    def approve_withdrawals(self, request, queryset):
        updated = queryset.filter(status=WithdrawalRequest.Status.PENDING).update(
            status=WithdrawalRequest.Status.APPROVED
        )
        self.message_user(request, f"{updated} withdrawal requests approved.")

    @admin.action(description=_("Reject selected withdrawal requests"))
    def reject_withdrawals(self, request, queryset):
        count = 0
        for withdrawal in queryset.filter(status=WithdrawalRequest.Status.PENDING):
            BillingService.refund(withdrawal)
            withdrawal.status = WithdrawalRequest.Status.REJECTED
            withdrawal.processed_at = timezone.now()
            withdrawal.admin_comment = "Rejected by admin."
            withdrawal.save(update_fields=["status", "processed_at", "admin_comment"])
            count += 1
        self.message_user(request, f"{count} withdrawal requests rejected and refunded.")

    @admin.action(description=_("Mark selected withdrawal requests as completed"))
    def complete_withdrawals(self, request, queryset):
        updated = queryset.filter(status=WithdrawalRequest.Status.APPROVED).update(
            status=WithdrawalRequest.Status.COMPLETED,
            processed_at=timezone.now(),
        )
        self.message_user(request, f"{updated} withdrawal requests marked as completed.")


@admin.register(TestBalanceGrant)
class TestBalanceGrantAdmin(admin.ModelAdmin):
    list_display = ("user", "amount", "granted_by", "granted_at", "note")
    list_filter = ("granted_by",)
    search_fields = ("user__email", "granted_by__email")
    readonly_fields = ("granted_at", "granted_by", "user", "amount")

    def has_add_permission(self, request):
        return False  # only via admin action, not manual form

    def has_change_permission(self, request, obj=None):
        return False  # audit log — immutable
