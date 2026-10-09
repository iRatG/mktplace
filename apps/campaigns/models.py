from datetime import timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

# Срок ответа на отклик и прямое предложение (решение бизнеса 07.10.2026, BZ-3): потом — EXPIRED.
RESPONSE_TTL = timedelta(days=7)
# За сколько до срока напомнить тому, кто должен ответить.
RESPONSE_REMINDER_BEFORE = timedelta(hours=24)


def response_deadline():
    """Срок ответа для нового отклика или предложения — сейчас + 7 дней.

    Миграция, добавившая поле, вызвала эту же функцию один раз для всех существующих строк:
    ожидавшие на момент выпуска получили «момент выпуска + 7 дней» (Р6).
    """
    return timezone.now() + RESPONSE_TTL


# Форматы контента и соцсети кампании — один список подписей для форм и шаблонов
# (фильтры content_type_label / social_label в apps/web/templatetags/labels.py).
CONTENT_TYPE_CHOICES = [
    ("post", "Пост"),
    ("stories", "Сторис"),
    ("video", "Видео"),
    ("review", "Обзор"),
    ("reels", "Reels"),
]
SOCIAL_CHOICES = [
    ("instagram", "Instagram"),
    ("telegram", "Telegram"),
    ("youtube", "YouTube"),
    ("vk", "ВКонтакте"),
    ("tiktok", "TikTok"),
]
# Доказательства исполнения, которые кампания требует вместе со ссылкой на публикацию.
EVIDENCE_CHOICES = [
    ("screenshot", "Скриншоты публикации"),
    ("statistics", "Статистика просмотров"),
    ("publication_time", "Подтверждение времени публикации"),
    ("retention", "Подтверждение сохранности публикации"),
]


class Campaign(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        MODERATION = "moderation", "На модерации"
        ACTIVE = "active", "Активна"
        PAUSED = "paused", "На паузе"
        COMPLETED = "completed", "Завершена"
        REJECTED = "rejected", "Отклонена"
        CANCELLED = "cancelled", "Отменена"

    class PaymentType(models.TextChoices):
        FIXED = "fixed", "Фиксированная"
        CPA = "cpa", "CPA"

    class CPAType(models.TextChoices):
        CLICK = "click", "Клик"
        LEAD = "lead", "Заявка"
        SALE = "sale", "Продажа"
        INSTALL = "install", "Установка"

    advertiser = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="campaigns",
        limit_choices_to={"role": "advertiser"},
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    category = models.ForeignKey(
        "platforms.Category",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="campaigns",
    )
    image = models.ImageField(upload_to="campaign_images/", null=True, blank=True)
    content_types = models.JSONField(
        default=list,
        help_text="List of allowed content types, e.g. ['post', 'stories', 'video', 'review']",
    )
    required_elements = models.JSONField(
        default=dict,
        help_text="Required elements in creative, e.g. {'hashtags': ['#brand'], 'mentions': ['@brand']}",
    )
    payment_type = models.CharField(
        max_length=10, choices=PaymentType.choices, default=PaymentType.FIXED
    )
    fixed_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    cpa_type = models.CharField(
        max_length=20, choices=CPAType.choices, null=True, blank=True
    )
    cpa_rate = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    cpa_tracking_url = models.URLField(blank=True)
    budget = models.DecimalField(
        max_digits=14, decimal_places=2, validators=[MinValueValidator(0)]
    )
    subject = models.CharField(
        max_length=255, blank=True,
        help_text="Что рекламируется: товар, услуга, бренд — заполняет рекламодатель",
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    # Окно приёма контента (решение бизнеса 06.10.2026): content_start … deadline.
    content_start = models.DateField(
        null=True, blank=True,
        help_text="Приём контента с",
    )
    deadline = models.DateField(
        null=True, blank=True,
        help_text="Приём контента до (конец окна); не позже чем за 5 рабочих дней до окончания кампании",
    )
    min_subscribers = models.PositiveIntegerField(default=0)
    min_er = models.DecimalField(
        max_digits=5, decimal_places=2, default=0.00,
        validators=[MinValueValidator(0)],
    )
    allowed_socials = models.JSONField(
        default=list,
        help_text="List of allowed social platforms, e.g. ['vk', 'telegram']",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    rejection_reason = models.TextField(blank=True)
    approved_snapshot = models.JSONField(
        null=True, blank=True,
        help_text="Параметры кампании (сырые данные CampaignForm) на момент последнего одобрения — "
                  "чтобы при повторной модерации показать «было → стало»",
    )
    max_bloggers = models.PositiveIntegerField(default=0)
    # Порядок согласования материала — условие оферты, переходит в сделку.
    approval_required = models.BooleanField(
        default=True, verbose_name="Согласовать материал перед публикацией",
        help_text="Без согласования рекламодателя публиковать нельзя",
    )
    content_lead_days = models.PositiveSmallIntegerField(
        default=5, validators=[MinValueValidator(1), MaxValueValidator(30)],
        verbose_name="Сдать материал за, рабочих дней до даты публикации",
    )
    review_days = models.PositiveSmallIntegerField(
        default=2, validators=[MinValueValidator(1), MaxValueValidator(10)],
        verbose_name="Срок рассмотрения материала, рабочих дней",
    )
    # Задание и требования к контенту — условия оферты, переходят в снимок оферты.
    content_units = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(100)],
        verbose_name="Количество единиц контента",
    )
    key_message = models.TextField(blank=True, verbose_name="Основное сообщение")
    mandatory_points = models.TextField(blank=True, verbose_name="Обязательные тезисы")
    disclosures = models.TextField(blank=True, verbose_name="Обязательные предупреждения и раскрытия")
    forbidden_phrases = models.TextField(blank=True, verbose_name="Запрещённые формулировки")
    content_restrictions = models.TextField(blank=True, verbose_name="Ограничения по тематике и содержанию")
    visual_requirements = models.TextField(blank=True, verbose_name="Требования к визуалу и монтажу")
    tags_requirements = models.TextField(blank=True, verbose_name="Хештеги, ссылки, отметки")
    # Приёмка и права.
    acceptance_criteria = models.TextField(blank=True, verbose_name="Критерии приёмки")
    min_retention_days = models.PositiveSmallIntegerField(
        default=3, validators=[MinValueValidator(1), MaxValueValidator(30)],
        verbose_name="Минимальный срок сохранения публикации, дней",
    )
    evidence_required = models.JSONField(
        default=list, blank=True, verbose_name="Доказательства исполнения",
        help_text="Что исполнитель прикладывает к ссылке на публикацию: screenshot, statistics, publication_time, retention",
    )

    class RightsOwner(models.TextChoices):
        BLOGGER = "blogger", "Исполнителю"
        ADVERTISER = "advertiser", "Рекламодателю (после оплаты)"

    rights_owner = models.CharField(
        max_length=20, choices=RightsOwner.choices, default=RightsOwner.BLOGGER,
        verbose_name="Исключительные права на контент",
    )
    reuse_allowed = models.BooleanField(default=False, verbose_name="Право на репост, таргет, адаптацию")
    completed_early_at = models.DateTimeField(
        null=True, blank=True, help_text="Когда рекламодатель завершил кампанию досрочно (не по сроку)",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Campaign"
        verbose_name_plural = "Campaigns"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.status})"

    @property
    def can_finish_early(self):
        """Рекламодатель может завершить досрочно: идёт, на паузе или на повторной модерации после одобрения (Р7)."""
        if self.status in (self.Status.ACTIVE, self.Status.PAUSED):
            return True
        return self.status == self.Status.MODERATION and bool(self.approved_snapshot)


class Response(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Ждёт решения"
        ACCEPTED = "accepted", "Принят"
        REJECTED = "rejected", "Отклонён"
        WITHDRAWN = "withdrawn", "Отозван"
        EXPIRED = "expired", "Истёк"  # кампания завершилась или прошёл срок ответа (expires_at)

    blogger = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="campaign_responses",
        limit_choices_to={"role": "blogger"},
    )
    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name="responses",
    )
    platform = models.ForeignKey(
        "platforms.Platform",
        on_delete=models.CASCADE,
        related_name="responses",
    )
    content_type = models.CharField(max_length=50)
    proposed_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    message = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    rejection_reason = models.TextField(
        blank=True, help_text="Комментарий рекламодателя при отклонении — его видит блогер",
    )
    expires_at = models.DateTimeField(default=response_deadline, help_text="Срок ответа — потом EXPIRED")
    reminder_sent_at = models.DateTimeField(null=True, blank=True, help_text="Когда напомнили о сроке ответа")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Campaign Response"
        verbose_name_plural = "Campaign Responses"
        constraints = [
            # Решение бизнеса 06.10.2026: после отклонения блогер может откликнуться снова.
            models.UniqueConstraint(
                fields=["blogger", "campaign", "platform"],
                condition=~Q(status__in=["withdrawn", "rejected", "expired"]),
                name="unique_active_response_per_platform",
            ),
            # …но ждать решения может только один его отклик на кампанию.
            models.UniqueConstraint(
                fields=["blogger", "campaign"],
                condition=Q(status="pending"),
                name="one_pending_response_per_campaign",
            ),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.blogger.email} -> {self.campaign.name} ({self.status})"

    @property
    def is_overdue(self):
        """Срок ответа прошёл — принять уже нельзя, даже если часовая задача ещё не перевела в EXPIRED."""
        return self.expires_at <= timezone.now()


class DirectOffer(models.Model):
    """Индивидуальная оферта рекламодателя исполнителю.

    Два пути: прямое предложение из каталога и оферта по принятому отклику (``response``). Сумма резервируется при
    направлении (``reserved_at``), сделка заключается только акцептом исполнителя. Оферты, направленные до этого
    правила, резерва не имеют — резервируют при акцепте.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Ждёт ответа"
        ACCEPTED = "accepted", "Принято"
        REJECTED = "rejected", "Отклонено"
        EXPIRED = "expired", "Истекло"  # кампания завершилась или прошёл срок ответа (expires_at)

    advertiser = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="direct_offers_sent",
        limit_choices_to={"role": "advertiser"},
    )
    blogger = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="direct_offers_received",
        limit_choices_to={"role": "blogger"},
    )
    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name="direct_offers",
    )
    platform = models.ForeignKey(
        "platforms.Platform",
        on_delete=models.CASCADE,
        related_name="direct_offers",
    )
    content_type = models.CharField(max_length=50)
    proposed_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    message = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    deal = models.OneToOneField(
        "deals.Deal",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="direct_offer",
    )
    expires_at = models.DateTimeField(default=response_deadline, help_text="Срок ответа — потом EXPIRED")
    reminder_sent_at = models.DateTimeField(null=True, blank=True, help_text="Когда напомнили о сроке ответа")
    response = models.OneToOneField(
        Response, on_delete=models.SET_NULL, null=True, blank=True, related_name="offer",
        help_text="Отклик, по которому направлена оферта (пусто — прямое предложение)",
    )
    publication_date = models.DateField(null=True, blank=True, help_text="Дата публикации")
    reserved_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    reserved_at = models.DateTimeField(null=True, blank=True, help_text="Когда сумма зарезервирована")
    commission_percent = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Ставка комиссии платформы, зафиксированная при направлении; пусто — оферта до тарифных уровней",
    )
    reserved_commission = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    @property
    def reserved_total(self):
        """Резерв под оферту: вознаграждение + комиссия (у оферт до тарифных уровней — только вознаграждение)."""
        return (self.reserved_amount or 0) + (self.reserved_commission or 0)
    terms = models.JSONField(default=dict, blank=True, help_text="Условия кампании на момент направления")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Direct Offer"
        verbose_name_plural = "Direct Offers"
        constraints = [
            models.UniqueConstraint(
                fields=["advertiser", "campaign", "platform"],
                condition=~Q(status__in=["rejected", "expired"]),
                name="unique_active_direct_offer_per_platform",
            ),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"DirectOffer {self.advertiser.email} → {self.blogger.email} ({self.status})"

    @property
    def is_overdue(self):
        """Срок ответа прошёл — принять уже нельзя, даже если часовая задача ещё не перевела в EXPIRED."""
        return self.expires_at <= timezone.now()


class CampaignEditProposal(models.Model):
    """Правки кампании, предложенные модератором (решение бизнеса 04.10.2026, issue #6).

    changes — {поле формы: {"old": <значение формы>, "new": <значение формы>}}: «сырые»
    значения CampaignForm (строка или список), чтобы при принятии прогнать их через ту же
    форму и ту же проверку параметров. Пока предложение PENDING, кампания остаётся в
    MODERATION и не одобряется/не отклоняется мимо ответа рекламодателя.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Ждёт ответа рекламодателя"
        ACCEPTED = "accepted", "Принято"
        DECLINED = "declined", "Отклонено"
        CLOSED = "closed", "Закрыто — кампания завершена"

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="edit_proposals")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="campaign_edit_proposals",
    )
    changes = models.JSONField(default=dict)
    comment = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Campaign Edit Proposal"
        verbose_name_plural = "Campaign Edit Proposals"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["campaign"], condition=Q(status="pending"),
                name="one_pending_edit_proposal_per_campaign",
            ),
        ]

    def __str__(self):
        return f"EditProposal #{self.pk} campaign={self.campaign_id} ({self.status})"
