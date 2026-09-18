from django.conf import settings
from django.db import models


class LegalEntityApplication(models.Model):
    """Заявка юрлица на регистрацию (название + ИНН) — точка входа с нуля.

    По макету бизнеса (task/bloger 12092026) это первый контакт компании с
    платформой: аккаунта ещё не существует, регистрация — это Название+ИНН,
    без email и пароля. Проверка ИНН вручную (временное решение) — поле
    оставлено простым CharField без внешней валидации, чтобы включить
    автоматическую проверку позже было доработкой, а не переделкой.

    Обмен договором идёт вне платформы через сервис «Ддокс» — ddocs_status
    сотрудник переключает вручную, интеграции с Ддокс нет. Аккаунт (`user`)
    создаётся только при выдаче доступа сотрудником (см.
    apps.web.views.registration.admin_legal_entity_issue_access) — до этого
    момента `user` пуст: заявка ещё не привязана ни к какому логину.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "На проверке"
        APPROVED = "approved", "Подтверждена"
        REJECTED = "rejected", "Отклонена"

    class DdocsStatus(models.TextChoices):
        NOT_SENT = "not_sent", "Договор не отправлен"
        CONTRACT_SENT = "contract_sent", "Договор отправлен"
        SIGNED = "signed", "Договор подписан"
        ACCESS_ISSUED = "access_issued", "Доступ выдан"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="legal_entity_applications",
        limit_choices_to={"role": "advertiser"},
        null=True,
        blank=True,
        help_text="Заполняется только при выдаче доступа — до этого аккаунта не существует",
    )
    company_name = models.CharField(max_length=255)
    inn = models.CharField(max_length=20, verbose_name="ИНН")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    rejection_reason = models.TextField(blank=True)

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_legal_entity_applications",
        limit_choices_to={"is_staff": True},
        help_text="Сотрудник, за которым закреплена заявка (назначается автоматически при подаче)",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_legal_entity_applications",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    ddocs_status = models.CharField(
        max_length=20, choices=DdocsStatus.choices, default=DdocsStatus.NOT_SENT
    )
    ddocs_note = models.TextField(
        blank=True, help_text="Свободный комментарий сотрудника о переписке в Ддокс"
    )

    # Срок хранения регистрационных данных — 3 года (подтверждено бизнесом,
    # то же правило, что и для Deal.last_distributed_at/is_frozen, закон «О рекламе»).
    retention_anchor_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Дата финального статуса заявки. От неё отсчитываются 3 года хранения.",
    )
    is_frozen = models.BooleanField(
        default=False,
        help_text="Заморожено (например активное разбирательство) — удаление запрещено.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Заявка юрлица на регистрацию"
        verbose_name_plural = "Заявки юрлиц на регистрацию"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.company_name} (ИНН {self.inn}) — {self.get_status_display()}"


class LegalEntityApplicationStatusLog(models.Model):
    application = models.ForeignKey(
        LegalEntityApplication,
        on_delete=models.CASCADE,
        related_name="status_logs",
    )
    old_status = models.CharField(max_length=20, blank=True)
    new_status = models.CharField(max_length=20)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="legal_entity_status_changes",
    )
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Смена статуса заявки юрлица"
        verbose_name_plural = "Смены статуса заявок юрлиц"
        ordering = ["created_at"]

    def __str__(self):
        return f"LegalEntityApplication#{self.application_id}: {self.old_status} -> {self.new_status}"

    @classmethod
    def log(cls, application, new_status, changed_by=None, comment=""):
        cls.objects.create(
            application=application,
            old_status=application.status,
            new_status=new_status,
            changed_by=changed_by,
            comment=comment,
        )


class IdentityVerification(models.Model):
    """Подтверждение личности блогера через OneID (id.egov.uz / MyID).

    Нужна ВСЕМ блогерам одинаково (с ИП и без) при регистрации — это
    подтверждение "это реальный человек", отдельно от проверки статуса ИП
    (см. IPApplication). Реальной интеграции с OneID пока нет — используется
    заглушка (см. apps.registration.services.get_oneid_backend), поэтому
    провал по существу невозможен сегодня, но состояние FAILED оставлено
    на будущее, когда появится реальный провайдер: тогда пользователь просто
    исправляет данные и пробует снова (без обращения в поддержку).
    """

    class Status(models.TextChoices):
        PENDING = "pending", "В обработке"
        VERIFIED = "verified", "Подтверждён"
        FAILED = "failed", "Не подтверждён"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="identity_verifications",
        help_text="Заполняется только после успешной верификации — аккаунт блогера создаётся именно тогда",
    )
    full_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=30)
    pinfl = models.CharField(max_length=14, verbose_name="ПИНФЛ")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    provider = models.CharField(max_length=30, default="oneid_stub")
    provider_reference = models.CharField(max_length=100, blank=True)
    failure_reason = models.TextField(blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    retention_anchor_at = models.DateTimeField(null=True, blank=True)
    is_frozen = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Подтверждение личности (OneID)"
        verbose_name_plural = "Подтверждения личности (OneID)"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.full_name} / {self.phone} — {self.get_status_display()}"


class IPApplication(models.Model):
    """Заявка блогера на подтверждение статуса ИП (патент/справка).

    Отдельный шаг ПОСЛЕ подтверждения личности через OneID (identity_verification
    должна быть VERIFIED). Проверяется сотрудником вручную по загруженному документу,
    общая очередь PENDING — в отличие от заявок юрлиц, персонального закрепления
    за сотрудником бизнес не просил.
    """

    class DocType(models.TextChoices):
        PATENT = "patent", "Патент"
        SPRAVKA = "spravka", "Справка"

    class Status(models.TextChoices):
        PENDING = "pending", "На проверке"
        APPROVED = "approved", "Подтверждена"
        REJECTED = "rejected", "Отклонена"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ip_applications",
        limit_choices_to={"role": "blogger"},
    )
    identity_verification = models.ForeignKey(
        IdentityVerification,
        on_delete=models.SET_NULL,
        related_name="ip_applications",
        null=True,
        blank=True,
        help_text="Должна быть VERIFIED перед подачей заявки на ИП",
    )
    document_type = models.CharField(max_length=20, choices=DocType.choices)
    document_number = models.CharField(max_length=100, blank=True)
    file = models.FileField(upload_to="ip_documents/%Y/%m/", help_text="PDF, JPG или PNG")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    rejection_reason = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_ip_applications",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    retention_anchor_at = models.DateTimeField(null=True, blank=True)
    is_frozen = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Заявка блогера на статус ИП"
        verbose_name_plural = "Заявки блогеров на статус ИП"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.email} — {self.get_document_type_display()} ({self.get_status_display()})"


class IPApplicationStatusLog(models.Model):
    application = models.ForeignKey(
        IPApplication,
        on_delete=models.CASCADE,
        related_name="status_logs",
    )
    old_status = models.CharField(max_length=20, blank=True)
    new_status = models.CharField(max_length=20)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ip_application_status_changes",
    )
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Смена статуса заявки на ИП"
        verbose_name_plural = "Смены статуса заявок на ИП"
        ordering = ["created_at"]

    def __str__(self):
        return f"IPApplication#{self.application_id}: {self.old_status} -> {self.new_status}"

    @classmethod
    def log(cls, application, new_status, changed_by=None, comment=""):
        cls.objects.create(
            application=application,
            old_status=application.status,
            new_status=new_status,
            changed_by=changed_by,
            comment=comment,
        )
