import secrets

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models


def _generate_token():
    return secrets.token_urlsafe(12)


def ticket_attachment_path(instance, filename):
    return f"tickets/{instance.ticket_id}/{filename}"


class Ticket(models.Model):
    """Внутренний тикет ИТ-команды: «хочу то-то» + скриншоты, вместо переписки в Telegram.

    Заводится участником группы IT Team, разбирается вручную (в сессии Claude Code).
    Если по ходу разбора возникают вопросы к бизнесу — на этот тикет вешаются
    BusinessQuery (см. ниже).
    """

    class Status(models.TextChoices):
        NEW = "new", "Новый"
        IN_PROGRESS = "in_progress", "В работе"
        WAITING_BUSINESS = "waiting_business", "Ждём бизнес"
        DONE = "done", "Закрыт"

    title = models.CharField(max_length=200)
    description = models.TextField(help_text="Что нужно сделать — свободный текст")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tickets_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Тикет"
        verbose_name_plural = "Тикеты"

    def __str__(self):
        return f"#{self.pk} {self.title}"


class TicketAttachment(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to=ticket_attachment_path, help_text="Скриншот или PDF")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Вложение"
        verbose_name_plural = "Вложения"

    def __str__(self):
        return self.file.name


class TicketStatusLog(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="status_logs")
    old_status = models.CharField(max_length=20, blank=True)
    new_status = models.CharField(max_length=20)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
    )
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Смена статуса тикета"
        verbose_name_plural = "Смены статуса тикета"

    def __str__(self):
        return f"Ticket#{self.ticket_id}: {self.old_status} -> {self.new_status}"

    @classmethod
    def log(cls, ticket, new_status, changed_by=None, comment=""):
        cls.objects.create(
            ticket=ticket,
            old_status=ticket.status,
            new_status=new_status,
            changed_by=changed_by,
            comment=comment,
        )


class BusinessQuery(models.Model):
    """Набор вопросов к бизнес-заказчику задачи (без аккаунта на платформе).

    Доступ — по ссылке с токеном + общий пароль на страницу.
    Может быть привязан к Ticket — один тикет может породить несколько раундов вопросов.
    """

    ticket = models.ForeignKey(
        Ticket, on_delete=models.CASCADE, related_name="queries", null=True, blank=True,
    )
    title = models.CharField(max_length=200)
    intro_text = models.TextField(blank=True, help_text="Вводный абзац на странице")
    source_task_path = models.CharField(
        max_length=255, blank=True,
        help_text="Например: task/registration 12092026 — к какой задаче относится",
    )
    token = models.SlugField(max_length=32, unique=True, default=_generate_token)
    password_hash = models.CharField(max_length=128, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Опросник для бизнеса"
        verbose_name_plural = "Опросники для бизнеса"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def set_password(self, raw_password):
        self.password_hash = make_password(raw_password)

    def check_password(self, raw_password):
        if not self.password_hash:
            return True
        return check_password(raw_password, self.password_hash)


class BusinessQuestion(models.Model):
    class Kind(models.TextChoices):
        CHOICE = "choice", "Выбор A/B"
        NOTE = "note", "Свободный текст"

    query = models.ForeignKey(BusinessQuery, on_delete=models.CASCADE, related_name="questions")
    order = models.PositiveIntegerField(default=0)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.CHOICE)
    title = models.CharField(max_length=255)
    context_text = models.TextField(blank=True, help_text="Поясняющий абзац (почему спрашиваем)")
    option_a = models.CharField(max_length=500, blank=True)
    option_b = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "Вопрос"
        verbose_name_plural = "Вопросы"

    def __str__(self):
        return self.title


class BusinessSubmission(models.Model):
    query = models.ForeignKey(BusinessQuery, on_delete=models.CASCADE, related_name="submissions")
    responder_name = models.CharField(max_length=150, blank=True)
    comment = models.TextField(blank=True)
    answers = models.JSONField(default=dict, help_text="{question_id: 'A'|'B'|'текст для note'}")
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-submitted_at"]
        verbose_name = "Ответ"
        verbose_name_plural = "Ответы"

    def __str__(self):
        return f"{self.responder_name or 'Без имени'} — {self.submitted_at:%d.%m.%Y %H:%M}"
