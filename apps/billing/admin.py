from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import SpecialTariff, TestBalanceGrant, Transaction, Wallet, WithdrawalRequest
from .services import BillingService


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("user", "is_demo_badge", "available_balance", "reserved_balance", "on_withdrawal", "updated_at")
    search_fields = ("user__email",)
    # Балансы меняются только через BillingService (с транзакцией) — здесь только чтение.
    readonly_fields = ("user", "available_balance", "reserved_balance", "on_withdrawal", "created_at", "updated_at")
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

    # Журнал денег неизменяем: записи создаёт только BillingService.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WithdrawalRequest)
class WithdrawalRequestAdmin(admin.ModelAdmin):
    list_display = ("blogger", "amount", "status", "created_at", "processed_at")
    list_filter = ("status",)
    search_fields = ("blogger__email",)
    # Статус и сумма меняются только действиями ниже — через BillingService (выплата пишет PAYOUT).
    readonly_fields = ("blogger", "amount", "status", "created_at", "updated_at", "processed_at")
    actions = ["complete_withdrawals", "reject_withdrawals"]

    def _process(self, request, queryset, method, done_label):
        done, errors = 0, []
        for withdrawal in queryset.filter(status=WithdrawalRequest.Status.PENDING):
            try:
                method(withdrawal, comment=f"{done_label} в Django admin ({request.user.email})")
                done += 1
            except ValueError as e:
                errors.append(f"#{withdrawal.pk}: {e}")
        self.message_user(request, f"{done_label}: {done}.")
        for err in errors:
            self.message_user(request, err, level="error")

    @admin.action(description=_("Выплачено (только ожидающие)"))
    def complete_withdrawals(self, request, queryset):
        self._process(request, queryset, BillingService.complete_withdrawal, "Выплачено")

    @admin.action(description=_("Отклонить и вернуть деньги (только ожидающие)"))
    def reject_withdrawals(self, request, queryset):
        self._process(request, queryset, BillingService.reject_withdrawal, "Отклонено")


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


@admin.register(SpecialTariff)
class SpecialTariffAdmin(admin.ModelAdmin):
    list_display = ("advertiser", "percent", "valid_until", "note")
    search_fields = ("advertiser__email",)
