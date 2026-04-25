from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class Wallet(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="wallet",
    )
    available_balance = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        validators=[MinValueValidator(0)],
    )
    reserved_balance = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        validators=[MinValueValidator(0)],
    )
    on_withdrawal = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        validators=[MinValueValidator(0)],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Wallet"
        verbose_name_plural = "Wallets"

    def __str__(self):
        return f"Wallet({self.user.email}): {self.available_balance}"

    @property
    def total_balance(self):
        return self.available_balance + self.reserved_balance + self.on_withdrawal


class Transaction(models.Model):
    class Type(models.TextChoices):
        DEPOSIT = "deposit", "Deposit"
        RESERVE = "reserve", "Reserve"
        RELEASE = "release", "Release"
        PAYMENT = "payment", "Payment"
        EARNING = "earning", "Earning"
        WITHDRAWAL = "withdrawal", "Withdrawal"
        REFUND = "refund", "Refund"
        CORRECTION = "correction", "Correction"
        TEST_CREDIT = "test_credit", "Test Credit (Demo)"

    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    type = models.CharField(max_length=20, choices=Type.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    balance_after = models.DecimalField(max_digits=14, decimal_places=2)
    deal = models.ForeignKey(
        "deals.Deal",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
    )
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Transaction"
        verbose_name_plural = "Transactions"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Transaction({self.type}, {self.amount}) for {self.wallet.user.email}"


class WithdrawalRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        COMPLETED = "completed", "Completed"

    blogger = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="withdrawal_requests",
        limit_choices_to={"role": "blogger"},
    )
    amount = models.DecimalField(
        max_digits=14, decimal_places=2, validators=[MinValueValidator(1)]
    )
    requisites = models.JSONField(
        help_text="Payment details, e.g. {'type': 'bank_card', 'card_number': '...'}"
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    processed_at = models.DateTimeField(null=True, blank=True)
    admin_comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Withdrawal Request"
        verbose_name_plural = "Withdrawal Requests"
        ordering = ["-created_at"]

    def __str__(self):
        return f"WithdrawalRequest({self.blogger.email}, {self.amount}, {self.status})"


class TestBalanceGrant(models.Model):
    """Records of test balance grants issued by admin to demo accounts."""

    MAX_TOTAL = 500_000  # max cumulative test credits per user

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="test_balance_grants",
        limit_choices_to={"is_demo": True},
    )
    amount = models.DecimalField(
        max_digits=14, decimal_places=2,
        validators=[MinValueValidator(1)],
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="issued_test_grants",
        limit_choices_to={"is_staff": True},
    )
    note = models.TextField(blank=True)
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Test Balance Grant"
        verbose_name_plural = "Test Balance Grants"
        ordering = ["-granted_at"]

    def __str__(self):
        return f"TestGrant({self.user.email}, +{self.amount}) by {self.granted_by_id}"
