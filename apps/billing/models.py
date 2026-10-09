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
        DEPOSIT = "deposit", "Пополнение"
        RESERVE = "reserve", "Резерв"
        RELEASE = "release", "Возврат резерва"
        PAYMENT = "payment", "Оплата сделки"
        EARNING = "earning", "Заработок"
        WITHDRAWAL = "withdrawal", "Вывод"
        REFUND = "refund", "Возврат вывода"
        CORRECTION = "correction", "Корректировка"
        TEST_CREDIT = "test_credit", "Test Credit (Demo)"
        PAYOUT = "payout", "Выплата"  # заявка на вывод выплачена: деньги ушли с «на выводе» из системы

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
    offer = models.ForeignKey(
        "campaigns.DirectOffer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
        help_text="Оферта, под которую зарезервировано или с которой возвращено",
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
        PENDING = "pending", "На рассмотрении"
        APPROVED = "approved", "Одобрена"
        REJECTED = "rejected", "Отклонена"
        COMPLETED = "completed", "Выплачена"

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

    MAX_TOTAL = 150_000_000  # max cumulative test credits per user (raised to cover 100M QA tester grants, 2026-09-13)

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


class PayoutRequisites(models.Model):
    """Подтверждённые реквизиты выплаты блогера (решение бизнеса 09.10.2026, T7/#39).

    По образцу apps.registration.models.IPApplication: заявка, общая очередь сотрудника,
    status pending/approved/rejected. «Подтверждённые реквизиты» = последняя заявка пользователя
    со статусом APPROVED — не отдельное поле, историю даёт сама таблица заявок.
    """

    class RequisiteType(models.TextChoices):
        CARD = "card", "Карта"
        ACCOUNT = "account", "Расчётный счёт"

    class Status(models.TextChoices):
        PENDING = "pending", "На проверке"
        APPROVED = "approved", "Подтверждена"
        REJECTED = "rejected", "Отклонена"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="payout_requisites",
        limit_choices_to={"role": "blogger"},
    )
    requisite_type = models.CharField(max_length=20, choices=RequisiteType.choices)

    # Карта — физлицо и самозанятый.
    card_number = models.CharField(max_length=16, blank=True, help_text="16 цифр, без пробелов")
    card_holder_name = models.CharField(max_length=255, blank=True)

    # Расчётный счёт — ИП и юрлицо-исполнитель.
    account_number = models.CharField(max_length=30, blank=True)
    bank_mfo = models.CharField(max_length=10, blank=True, verbose_name="МФО банка")
    bank_inn = models.CharField(max_length=20, blank=True, verbose_name="ИНН банка")
    bank_name = models.CharField(max_length=255, blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    rejection_reason = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_payout_requisites",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Реквизиты выплаты"
        verbose_name_plural = "Реквизиты выплаты"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status="pending"),
                name="one_pending_payout_requisites_per_user",
            ),
        ]

    def __str__(self):
        return f"PayoutRequisites({self.user.email}, {self.requisite_type}, {self.status})"

    def masked_number(self):
        """Для отображения в кошельке — не для хранения или сверки."""
        if self.requisite_type == self.RequisiteType.CARD and self.card_number:
            return f"•••• {self.card_number[-4:]}"
        if self.requisite_type == self.RequisiteType.ACCOUNT and self.account_number:
            return f"•••• {self.account_number[-4:]}"
        return ""

    def as_snapshot(self):
        """JSON-снимок для WithdrawalRequest.requisites — копия, не ссылка на эту заявку."""
        if self.requisite_type == self.RequisiteType.CARD:
            return {
                "type": "card",
                "card_number": self.card_number,
                "card_holder_name": self.card_holder_name,
            }
        return {
            "type": "account",
            "account_number": self.account_number,
            "bank_mfo": self.bank_mfo,
            "bank_inn": self.bank_inn,
            "bank_name": self.bank_name,
        }
