from django import forms
from django.contrib import admin

from .models import (
    BusinessQuery,
    BusinessQuestion,
    BusinessSubmission,
    Ticket,
    TicketAttachment,
    TicketStatusLog,
)


class TicketAttachmentInline(admin.TabularInline):
    model = TicketAttachment
    extra = 0


class TicketStatusLogInline(admin.TabularInline):
    model = TicketStatusLog
    extra = 0
    readonly_fields = ("old_status", "new_status", "changed_by", "comment", "created_at")
    can_delete = False


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "status", "created_by", "created_at")
    list_filter = ("status",)
    search_fields = ("title", "description", "created_by__email")
    inlines = [TicketAttachmentInline, TicketStatusLogInline]


class BusinessQuestionInline(admin.TabularInline):
    model = BusinessQuestion
    extra = 1


class BusinessSubmissionInline(admin.TabularInline):
    model = BusinessSubmission
    extra = 0
    readonly_fields = ("responder_name", "comment", "answers", "submitted_at")
    can_delete = False


class BusinessQueryAdminForm(forms.ModelForm):
    """Добавляет нехранимое поле new_password — при заполнении хэширует пароль опросника."""

    new_password = forms.CharField(
        required=False, widget=forms.PasswordInput,
        label="Новый пароль", help_text="Оставьте пустым, чтобы не менять текущий пароль",
    )

    class Meta:
        model = BusinessQuery
        fields = ["ticket", "title", "intro_text", "source_task_path", "token", "is_active"]

    def save(self, commit=True):
        instance = super().save(commit=False)
        new_password = self.cleaned_data.get("new_password")
        if new_password:
            instance.set_password(new_password)
        if commit:
            instance.save()
        return instance


@admin.register(BusinessQuery)
class BusinessQueryAdmin(admin.ModelAdmin):
    form = BusinessQueryAdminForm
    list_display = ("title", "ticket", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("title", "source_task_path")
    inlines = [BusinessQuestionInline, BusinessSubmissionInline]
