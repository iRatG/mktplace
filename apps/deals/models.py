import uuid as _uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


def _generate_slug():
    return _uuid.uuid4().hex[:16]


class Deal(models.Model):
    class Status(models.TextChoices):
        WAITING_PAYMENT = "waiting_payment", "Ожидает оплаты"
        IN_PROGRESS = "in_progress", "В работе"
        ON_APPROVAL = "on_approval", "На согласовании"
        WAITING_PUBLICATION = "waiting_publication", "Ждёт публикации"
        PUBLISHED = "published", "Опубликована"
        CHECKING = "checking", "На проверке"
        COMPLETED = "completed", "Завершена"
        DISPUTED = "disputed", "Оспорена"
        CANCELLED = "cancelled", "Отменена"

    campaign = models.ForeignKey(
        "campaigns.Campaign",
        on_delete=models.PROTECT,
        related_name="deals",
    )
    blogger = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="deals_as_blogger",
        limit_choices_to={"role": "blogger"},
    )
    platform = models.ForeignKey(
        "platforms.Platform",
        on_delete=models.PROTECT,
        related_name="deals",
    )
    advertiser = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="deals_as_advertiser",
        limit_choices_to={"role": "advertiser"},
    )
    response = models.OneToOneField(
        "campaigns.Response",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deal",
    )
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(0)]
    )
    status = models.CharField(
        max_length=30, choices=Status.choices, default=Status.WAITING_PAYMENT
    )

    # Creative fields
    creative_text = models.TextField(blank=True)
    creative_media = models.FileField(
        upload_to="deal_creatives/", null=True, blank=True
    )
    creative_submitted_at = models.DateTimeField(null=True, blank=True)
    creative_approved_at = models.DateTimeField(null=True, blank=True)
    creative_rejection_reason = models.TextField(blank=True)
    creative_submissions = models.PositiveSmallIntegerField(default=0, help_text="Сколько раз материал отправлен на согласование")
    review_overdue_notified_at = models.DateTimeField(
        null=True, blank=True, help_text="Когда уведомили стороны, что срок рассмотрения материала истёк",
    )

    # Порядок согласования — условия оферты на момент заключения (сделки до правила — без обязательного согласования).
    approval_required = models.BooleanField(default=False)
    content_lead_days = models.PositiveSmallIntegerField(default=5)
    review_days = models.PositiveSmallIntegerField(default=2)

    # Publication fields
    publication_date = models.DateField(null=True, blank=True, help_text="Дата публикации из оферты")
    publication_reminder_sent_at = models.DateTimeField(
        null=True, blank=True, help_text="Когда напомнили исполнителю о дате публикации (сбрасывается при переносе)",
    )
    overdue_notified_at = models.DateTimeField(
        null=True, blank=True, help_text="Когда уведомили стороны о просрочке даты публикации (сбрасывается при переносе)",
    )
    publication_url = models.URLField(blank=True)
    publication_at = models.DateTimeField(null=True, blank=True)
    publication_accepted_at = models.DateTimeField(
        null=True, blank=True, help_text="Рекламодатель принял публикацию (без оплаты — оплата по сроку)",
    )
    # Размещение по условиям оферты; None — сделка до правила: оплата через 72 часа или при подтверждении.
    min_retention_days = models.PositiveSmallIntegerField(null=True, blank=True)
    evidence_required = models.JSONField(default=list, blank=True)

    # Dispute fields
    dispute_reason = models.TextField(blank=True)
    dispute_opened_at = models.DateTimeField(null=True, blank=True)
    dispute_resolved_at = models.DateTimeField(null=True, blank=True)
    dispute_resolution = models.TextField(blank=True)
    paid_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Сколько фактически перечислено исполнителю (раздел суммы, компенсация); пусто — вся сумма или ничего",
    )

    # Data retention fields (REQ-5 — Закон «О рекламе» ст.15, 3 года хранения)
    last_distributed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Дата последнего распространения рекламы. От этой даты отсчитывается 3-летний срок хранения материалов.",
    )
    is_frozen = models.BooleanField(
        default=False,
        help_text="Материалы заморожены (активный или завершённый спор) — удаление запрещено до истечения 3 лет.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Deal"
        verbose_name_plural = "Deals"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Deal#{self.pk} {self.blogger.email} / {self.campaign.name} ({self.status})"

    # Публикации ещё нет — дату можно перенести, о ней напоминают.
    UNPUBLISHED_STATUSES = (Status.IN_PROGRESS, Status.ON_APPROVAL, Status.WAITING_PUBLICATION)
    # Дата прошла в этих статусах — просрочка; «На согласовании» ход за рекламодателем — не просрочка.
    OVERDUE_STATUSES = (Status.IN_PROGRESS, Status.WAITING_PUBLICATION)

    @property
    def overdue_days(self):
        """На сколько дней просрочена дата публикации (0 — не просрочена)."""
        from django.utils import timezone

        if not self.publication_date or self.status not in self.OVERDUE_STATUSES:
            return 0
        return max((timezone.localdate() - self.publication_date).days, 0)

    @property
    def content_due(self):
        """Последний день сдачи материала на согласование — или None (согласование не обязательно, нет даты)."""
        if not self.approval_required or not self.publication_date:
            return None
        from apps.campaigns.validation import working_days_before

        return working_days_before(self.publication_date, self.content_lead_days)

    @property
    def review_due(self):
        """До какого момента рекламодатель рассматривает отправленный материал — или None."""
        if not self.approval_required or self.status != self.Status.ON_APPROVAL or not self.creative_submitted_at:
            return None
        from apps.campaigns.validation import working_days_after

        return working_days_after(self.creative_submitted_at, self.review_days)

    @property
    def review_overdue(self):
        from django.utils import timezone

        due = self.review_due
        return bool(due and due <= timezone.now())

    @property
    def claim_until(self):
        """До какого момента подаётся претензия по размещению (3 рабочих дня с загрузки подтверждения) — или None."""
        if self.min_retention_days is None or not self.publication_at:
            return None
        from apps.campaigns.validation import working_days_after

        return working_days_after(self.publication_at, CLAIM_WORKING_DAYS)

    @property
    def retention_until(self):
        """До какого момента исполнитель сохраняет публикацию (минимальный срок сохранения) — или None."""
        if self.min_retention_days is None or not self.publication_at:
            return None
        from datetime import timedelta

        return self.publication_at + timedelta(days=self.min_retention_days)

    @property
    def payout_due(self):
        """Когда исполнителю перечисляются деньги, если нет претензии: по позднему из сроков — претензионного (если
        публикацию не приняли раньше) и срока сохранения. Сделки до правила — 72 часа после публикации."""
        from datetime import timedelta

        if not self.publication_at:
            return None
        if self.min_retention_days is None:
            return self.publication_at + timedelta(hours=72)
        if self.publication_accepted_at:
            return self.retention_until
        return max(self.claim_until, self.retention_until)

    @property
    def open_claim(self):
        """Претензия, которая рассматривается сейчас, — или None."""
        return self.claims.filter(status__in=["open", "extra_docs"]).select_related("author").first()

    @property
    def pending_date_change(self):
        """Ожидающее ответа предложение перенести дату публикации — или None."""
        return self.date_changes.filter(status=PublicationDateChange.Status.PENDING).select_related("proposed_by").first()


CLAIM_WORKING_DAYS = 3
# Сроки претензии на платформе (рабочие дни): объяснения второй стороны, решение сотрудника, доп. документирование.
CLAIM_ANSWER_WORKING_DAYS = 2
CLAIM_DECISION_WORKING_DAYS = 5
CLAIM_EXTRA_DOCS_WORKING_DAYS = 7
COMPENSATION_PERCENT = 30


class Claim(models.Model):
    """Претензия по сделке: мотивированное возражение стороны с доказательствами; решение принимает сотрудник."""

    class Subject(models.TextChoices):
        PLACEMENT = "placement", "Размещение не соответствует условиям оферты"
        RETENTION = "retention", "Публикация удалена или скрыта раньше срока сохранения"
        DEADLINE = "deadline", "Нарушен срок"
        UNAPPROVED = "unapproved", "Размещён несогласованный материал"
        NO_REVIEW = "no_review", "Рекламодатель не рассматривает материал после правок"
        NOT_ACCEPTED = "not_accepted", "Исполнение не принимается без оснований"
        OTHER = "other", "Другое"

    class Demand(models.TextChoices):
        REFUND = "refund", "Вернуть деньги рекламодателю"
        PARTIAL_REFUND = "partial_refund", "Частичный возврат"
        REFUSE_PAYOUT = "refuse_payout", "Отказать в выплате"
        REVISION = "revision", "Дополнительная доработка"
        PAYOUT = "payout", "Перечислить оплату исполнителю"
        COMPENSATION = "compensation", "Компенсация за выполненную работу"
        OTHER = "other", "Иное"

    class Status(models.TextChoices):
        OPEN = "open", "Рассматривается"
        EXTRA_DOCS = "extra_docs", "Дополнительное документирование"
        RESOLVED = "resolved", "Решение принято"

    class Decision(models.TextChoices):
        TO_BLOGGER = "to_blogger", "Перечислить исполнителю в полном объёме"
        TO_ADVERTISER = "to_advertiser", "Вернуть рекламодателю в полном объёме"
        SPLIT = "split", "Распределить между сторонами"
        COMPENSATION = "compensation", "Компенсация исполнителю за выполненную работу"

    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, related_name="claims")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="claims")
    subject = models.CharField(max_length=30, choices=Subject.choices)
    violated_term = models.CharField(max_length=255, help_text="Какое условие оферты или правило нарушено")
    description = models.TextField()
    demand = models.CharField(max_length=30, choices=Demand.choices)
    demand_details = models.TextField(blank=True)
    links = models.TextField(blank=True, help_text="Ссылки на доказательства, по одной в строке")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    answer_until = models.DateTimeField()
    decide_until = models.DateTimeField()
    explanation = models.TextField(blank=True)
    explained_at = models.DateTimeField(null=True, blank=True)
    extra_docs_until = models.DateTimeField(null=True, blank=True)
    decision = models.CharField(max_length=20, choices=Decision.choices, blank=True)
    blogger_part = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    decision_comment = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="decided_claims",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reminder_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["deal"], condition=models.Q(status__in=["open", "extra_docs"]), name="one_open_claim_per_deal",
            ),
        ]

    def __str__(self):
        return f"Claim#{self.pk} deal={self.deal_id} ({self.status})"

    @property
    def respondent(self):
        return self.deal.advertiser if self.author_id == self.deal.blogger_id else self.deal.blogger

    @property
    def link_list(self):
        return [line.strip() for line in self.links.splitlines() if line.strip()]

    @property
    def compensation_grounds(self):
        """Есть ли признаки ПЭТ-компенсации: исполнитель отправлял материал повторно, а срок рассмотрения истёк."""
        deal = self.deal
        return deal.creative_submissions >= 2 and deal.review_overdue


class ClaimFile(models.Model):
    """Доказательство к претензии или объяснениям: скриншоты, статистика, переписка, видео- и аудиоматериалы."""

    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="files")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    file = models.FileField(upload_to="claim_files/%Y/%m/")
    note = models.CharField(max_length=255, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at"]


class DealEvidence(models.Model):
    """Доказательство исполнения к публикации: скриншот, статистика, подтверждение времени или сохранности."""

    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, related_name="evidence")
    kind = models.CharField(max_length=30)
    file = models.FileField(upload_to="deal_evidence/%Y/%m/")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at"]

    @property
    def kind_label(self):
        from apps.campaigns.models import EVIDENCE_CHOICES

        return dict(EVIDENCE_CHOICES).get(self.kind, self.kind)

    def __str__(self):
        return f"Deal#{self.deal_id} {self.kind}"


class PublicationDateChange(models.Model):
    """Предложение перенести дату публикации — вступает в силу, только когда вторая сторона согласилась."""

    class Status(models.TextChoices):
        PENDING = "pending", "Ждёт ответа"
        ACCEPTED = "accepted", "Принято"
        DECLINED = "declined", "Отклонено"
        CLOSED = "closed", "Закрыто"

    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, related_name="date_changes")
    proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="proposed_date_changes",
    )
    old_date = models.DateField()
    new_date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    answered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["deal"], condition=models.Q(status="pending"), name="one_pending_date_change_per_deal",
            ),
        ]

    def __str__(self):
        return f"Deal#{self.deal_id}: {self.old_date} → {self.new_date} ({self.status})"


class DealStatusLog(models.Model):
    deal = models.ForeignKey(
        Deal,
        on_delete=models.CASCADE,
        related_name="status_logs",
    )
    old_status = models.CharField(max_length=30, blank=True)
    new_status = models.CharField(max_length=30)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deal_status_changes",
    )
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Deal Status Log"
        verbose_name_plural = "Deal Status Logs"
        ordering = ["created_at"]

    def __str__(self):
        return f"Deal#{self.deal_id}: {self.old_status} -> {self.new_status}"

    @classmethod
    def log(cls, deal, new_status, changed_by=None, comment=""):
        cls.objects.create(
            deal=deal,
            old_status=deal.status,
            new_status=new_status,
            changed_by=changed_by,
            comment=comment,
        )


class ChatMessage(models.Model):
    deal = models.ForeignKey(
        Deal,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="sent_deal_messages",
    )
    text = models.TextField(blank=True)
    file = models.FileField(upload_to="deal_chat_files/", null=True, blank=True)
    is_system = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Chat Message"
        verbose_name_plural = "Chat Messages"
        ordering = ["created_at"]

    def __str__(self):
        return f"Message in Deal#{self.deal_id} by {getattr(self.sender, 'email', 'system')}"


class Review(models.Model):
    """Отзыв рекламодателя о блогере после завершения сделки (Модуль 7).

    Создаётся в течение 7 дней после перехода сделки в статус COMPLETED.
    Один отзыв на одну сделку (OneToOne к Deal).

    author — рекламодатель, оставивший отзыв.
    target — блогер, получивший отзыв.
    rating — оценка от 1 до 5.
    text   — произвольный текст отзыва (необязательно).

    После создания пересчитывается BloggerProfile.rating (среднее всех оценок).
    """

    deal = models.OneToOneField(
        Deal,
        on_delete=models.CASCADE,
        related_name="review",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reviews_written",
    )
    target = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reviews_received",
    )
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Review"
        verbose_name_plural = "Reviews"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Review#{self.pk} by {self.author.email} → {self.target.email} ({self.rating}★)"


# ── CPA Tracking (Sprint 8) ──────────────────────────────────────────────────

class TrackingLink(models.Model):
    """Уникальная трекинговая ссылка для CPA-сделки.

    Создаётся лениво при первом открытии страницы сделки (get_or_create).
    Slug — 16-символьный hex UUID, используется в публичном URL /t/<slug>/.
    """

    deal = models.OneToOneField(
        Deal,
        on_delete=models.CASCADE,
        related_name="tracking_link",
    )
    slug = models.CharField(
        max_length=16,
        unique=True,
        db_index=True,
        default=_generate_slug,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Tracking Link"
        verbose_name_plural = "Tracking Links"

    def __str__(self):
        return f"TrackingLink#{self.pk} slug={self.slug} deal={self.deal_id}"

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse("web:cpa_click_track", kwargs={"slug": self.slug})


class ClickLog(models.Model):
    """Один клик по трекинговой ссылке."""

    tracking_link = models.ForeignKey(
        TrackingLink,
        on_delete=models.CASCADE,
        related_name="clicks",
    )
    click_id = models.UUIDField(unique=True, default=_uuid.uuid4, db_index=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Click Log"
        verbose_name_plural = "Click Logs"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Click {self.click_id} on TrackingLink#{self.tracking_link_id}"


class Conversion(models.Model):
    """Конверсия по CPA-сделке.

    Типы:
    - CLICK  — мгновенная оплата при клике (без постбека)
    - LEAD   — лид, подтверждается постбеком
    - SALE   — продажа, подтверждается постбеком
    - INSTALL — установка приложения, подтверждается постбеком

    credited=True означает что BillingService.credit_cpa_conversion уже начислил.
    """

    class ConversionType(models.TextChoices):
        CLICK = "click", "Клик"
        LEAD = "lead", "Заявка"
        SALE = "sale", "Продажа"
        INSTALL = "install", "Установка"

    tracking_link = models.ForeignKey(
        TrackingLink,
        on_delete=models.CASCADE,
        related_name="conversions",
    )
    click_log = models.ForeignKey(
        ClickLog,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversions",
    )
    conversion_type = models.CharField(
        max_length=20,
        choices=ConversionType.choices,
        default=ConversionType.CLICK,
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    credited = models.BooleanField(default=False)
    postback_raw = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Conversion"
        verbose_name_plural = "Conversions"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["click_log", "conversion_type"],
                name="unique_conversion_per_click_and_type",
            ),
        ]

    def __str__(self):
        return f"Conversion#{self.pk} type={self.conversion_type} amount={self.amount} credited={self.credited}"
