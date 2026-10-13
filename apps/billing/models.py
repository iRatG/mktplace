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
    """Выплата исполнителю (с 13.10.2026 — формируется системой по сделке, не заявкой блогера).

    Одна строка реестра выплат для бухгалтера: сумма уже «к выплате» (on_withdrawal), сотрудник проводит
    перевод в банке и отмечает «проведена» (PAYOUT) или отклоняет с причиной (деньги — обратно на баланс).
    kind=MANUAL остаётся для остатков на балансе до перехода (ставит в реестр сотрудник).
    """

    class Status(models.TextChoices):
        PENDING = "pending", "К выплате"
        APPROVED = "approved", "Одобрена"  # не используется, значение оставлено для старых строк
        REJECTED = "rejected", "Отклонена"
        COMPLETED = "completed", "Проведена"

    class Kind(models.TextChoices):
        MANUAL = "manual", "Остаток на балансе"
        DEAL = "deal", "Выплата по сделке"

    blogger = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="withdrawal_requests",
        limit_choices_to={"role": "blogger"},
    )
    deal = models.ForeignKey(
        "deals.Deal",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payouts",
        help_text="Сделка, по которой сформирована выплата",
    )
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.MANUAL)
    amount = models.DecimalField(
        max_digits=14, decimal_places=2, validators=[MinValueValidator(1)]
    )
    requisites = models.JSONField(
        default=dict, blank=True,
        help_text="Снимок подтверждённых реквизитов; пустой — заполняется при подтверждении реквизитов или проведении",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    processed_at = models.DateTimeField(null=True, blank=True)
    admin_comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Выплата"
        verbose_name_plural = "Выплаты"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["deal"],
                condition=~models.Q(status="rejected") & models.Q(deal__isnull=False),
                name="one_open_payout_per_deal",
            ),
        ]

    def __str__(self):
        return f"WithdrawalRequest({self.blogger.email}, {self.amount}, {self.status})"

    @property
    def is_deal_payout(self):
        return self.kind == self.Kind.DEAL

    def effective_requisites(self):
        """Снимок реквизитов для проведения: свой, а если пустой — текущие подтверждённые реквизиты блогера."""
        if self.requisites:
            return self.requisites
        from .payout_requisites import current_requisites

        current = current_requisites(self.blogger)
        return current.as_snapshot() if current else {}

    @property
    def blocked_reasons(self):
        """Почему выплату нельзя провести: нет реквизитов, ЭСФ исполнителя не подтверждена. Пусто — можно."""
        reasons = []
        if not self.effective_requisites():
            reasons.append("нет подтверждённых реквизитов")
        if self.deal_id:
            einvoice = next(
                (e for e in self.deal.einvoices.all() if e.kind == EInvoice.Kind.PERFORMER), None,
            )
            if einvoice is not None:
                if einvoice.status == EInvoice.Status.WAITING:
                    reasons.append("ждёт ЭСФ исполнителя")
                elif einvoice.status == EInvoice.Status.REJECTED:
                    reasons.append("ЭСФ отклонена")
                elif einvoice.status == EInvoice.Status.MISMATCH:
                    reasons.append("ЭСФ: расхождение")
        return reasons

    @property
    def is_ready(self):
        return not self.blocked_reasons


class EInvoice(models.Model):
    """ЭСФ (электронная счёт-фактура) по оплаченной сделке — без денег, только документ и его статус.

    PERFORMER — ЭСФ исполнителя рекламодателю на вознаграждение: условие выплаты для ИП, юрлица и самозанятого
    (ESF_REQUIRED_CATEGORIES); PLATFORM — ЭСФ платформы рекламодателю на комиссию, «к выставлению» после проведения
    выплаты. Статус пока вносит сотрудник (provider «manual»); оператор ЭДО — отдельный провайдер (apps/billing/esf.py).
    """

    class Kind(models.TextChoices):
        PERFORMER = "performer", "ЭСФ исполнителя рекламодателю"
        PLATFORM = "platform", "ЭСФ платформы за комиссию"

    class Status(models.TextChoices):
        NOT_REQUIRED = "not_required", "Не требуется"
        TO_ISSUE = "to_issue", "К выставлению"
        WAITING = "waiting", "Ждёт подтверждения"
        CONFIRMED = "confirmed", "Подтверждена"
        REJECTED = "rejected", "Отклонена"
        MISMATCH = "mismatch", "Расхождение"

    deal = models.ForeignKey("deals.Deal", on_delete=models.PROTECT, related_name="einvoices")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    status = models.CharField(max_length=15, choices=Status.choices)
    contract_number = models.CharField(max_length=30, help_text="Номер договора (оферты) на момент создания")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    number = models.CharField(max_length=64, blank=True, help_text="Номер ЭСФ у оператора ЭДО")
    provider = models.CharField(max_length=20, default="manual")
    external_id = models.CharField(max_length=64, blank=True)
    note = models.TextField(blank=True)
    checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="checked_einvoices",
    )
    status_changed_at = models.DateTimeField(null=True, blank=True)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "ЭСФ"
        verbose_name_plural = "ЭСФ"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["deal", "kind"], name="one_einvoice_per_deal_kind"),
        ]

    def __str__(self):
        return f"EInvoice({self.get_kind_display()}, deal #{self.deal_id}, {self.status})"

    @property
    def is_blocking(self):
        return self.kind == self.Kind.PERFORMER and self.status in (
            self.Status.WAITING, self.Status.REJECTED, self.Status.MISMATCH,
        )


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


class SpecialTariff(models.Model):
    """Специальная ставка комиссии рекламодателя вместо тарифных уровней (задаёт сотрудник)."""

    advertiser = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="special_tariff")
    percent = models.DecimalField(max_digits=5, decimal_places=2, validators=[MinValueValidator(0)])
    valid_until = models.DateField(null=True, blank=True, help_text="Пусто — бессрочно")
    note = models.CharField(max_length=255, blank=True, help_text="Основание: договор, скидка и т. п.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Special tariff"
        verbose_name_plural = "Special tariffs"

    def __str__(self):
        return f"{self.advertiser} — {self.percent}%"
