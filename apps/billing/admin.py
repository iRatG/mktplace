from django.contrib import admin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Transaction, Wallet, WithdrawalRequest


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("user", "available_balance", "reserved_balance", "on_withdrawal", "updated_at")
    search_fields = ("user__email",)
    readonly_fields = ("created_at", "updated_at")


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
        from .services import BillingService

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
