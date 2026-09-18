"""Views модуля регистрации юрлиц и блогеров (ИП).

Рекламодатель (юрлицо):
    /profile/legal-entity/             — подать заявку (название + ИНН)
    /panel/legal-entities/             — очередь заявок, закреплённых за текущим сотрудником
    /panel/legal-entities/<pk>/approve/
    /panel/legal-entities/<pk>/reject/
    /panel/legal-entities/<pk>/ddocs/          — вручную переключить статус обмена через «Ддокс»
    /panel/legal-entities/<pk>/issue-access/   — выдать пароль (показывается один раз)

Блогер:
    /register/blogger/verify/          — подтверждение личности (OneID/ПИНФЛ), создаёт аккаунт
    /profile/ip-application/           — загрузить документ на статус ИП
    /profile/ip-application/list/      — список своих заявок на ИП
    /panel/ip-applications/            — очередь заявок на проверку
    /panel/ip-applications/<pk>/approve/
    /panel/ip-applications/<pk>/reject/

Внешние сервисы (Ддокс, OneID/MyID, SMS) на этом этапе не подключены —
см. apps.registration.services (заглушки).
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.notifications.service import NotificationService
from apps.profiles.models import BloggerProfile
from apps.registration.models import (
    IdentityVerification,
    IPApplication,
    IPApplicationStatusLog,
    LegalEntityApplication,
    LegalEntityApplicationStatusLog,
)
from apps.registration.services import assign_reviewer, get_oneid_backend
from apps.registration.tasks import send_blogger_sms_credentials
from apps.users.models import User

from ..forms import (
    AdminIPApplicationRejectForm,
    AdminLegalEntityRejectForm,
    BloggerIdentitySubmitForm,
    IPApplicationForm,
    LegalEntityApplicationForm,
)
from .admin_panel import _staff_required


# ── Юрлицо: рекламодатель ────────────────────────────────────────────────────

@login_required
def legal_entity_submit(request):
    """Подать заявку на регистрацию юрлица (название + ИНН)."""
    if request.user.role != User.Role.ADVERTISER:
        messages.error(request, "Доступно только рекламодателям.")
        return redirect("web:advertiser_dashboard")

    applications = LegalEntityApplication.objects.filter(user=request.user).order_by("-created_at")

    form = LegalEntityApplicationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        application = form.save(commit=False)
        application.user = request.user
        application.save()

        reviewer = assign_reviewer()
        if reviewer:
            application.assigned_to = reviewer
            application.save(update_fields=["assigned_to"])
            NotificationService.notify_legal_entity_assigned(reviewer, application)

        messages.success(request, "Заявка отправлена на проверку.")
        return redirect("web:legal_entity_submit")

    return render(
        request, "registration/legal_entity_submit.html",
        {"form": form, "applications": applications},
    )


def _legal_entity_queue(user):
    """Очередь заявок юрлиц, закреплённых за сотрудником.

    Включает PENDING (нужно одобрить/отклонить) и APPROVED, доступ по которым
    ещё не выдан (нужно довести обмен через «Ддокс» до конца) — заявка не
    исчезает из личной очереди сотрудника, пока доступ не выдан.
    """
    return (
        LegalEntityApplication.objects.filter(assigned_to=user)
        .exclude(ddocs_status=LegalEntityApplication.DdocsStatus.ACCESS_ISSUED)
        .filter(status__in=[LegalEntityApplication.Status.PENDING, LegalEntityApplication.Status.APPROVED])
        .select_related("user")
        .order_by("created_at")
    )


@_staff_required
def admin_legal_entities(request):
    return render(
        request, "admin_panel/legal_entities.html",
        {
            "applications": _legal_entity_queue(request.user),
            "ddocs_status_choices": LegalEntityApplication.DdocsStatus.choices,
        },
    )


@_staff_required
@require_POST
def admin_legal_entity_approve(request, pk):
    application = get_object_or_404(
        LegalEntityApplication, pk=pk, status=LegalEntityApplication.Status.PENDING
    )
    LegalEntityApplicationStatusLog.log(
        application, LegalEntityApplication.Status.APPROVED, changed_by=request.user,
    )
    application.status = LegalEntityApplication.Status.APPROVED
    application.reviewed_by = request.user
    application.reviewed_at = timezone.now()
    application.rejection_reason = ""
    application.save(update_fields=["status", "reviewed_by", "reviewed_at", "rejection_reason", "updated_at"])
    NotificationService.notify_legal_entity_approved(application.user, application)
    messages.success(request, f"Заявка «{application.company_name}» подтверждена.")
    return redirect("web:admin_legal_entities")


@_staff_required
@require_POST
def admin_legal_entity_reject(request, pk):
    application = get_object_or_404(
        LegalEntityApplication, pk=pk, status=LegalEntityApplication.Status.PENDING
    )
    form = AdminLegalEntityRejectForm(request.POST)
    if form.is_valid():
        LegalEntityApplicationStatusLog.log(
            application, LegalEntityApplication.Status.REJECTED,
            changed_by=request.user, comment=form.cleaned_data["rejection_reason"],
        )
        application.status = LegalEntityApplication.Status.REJECTED
        application.reviewed_by = request.user
        application.reviewed_at = timezone.now()
        application.rejection_reason = form.cleaned_data["rejection_reason"]
        application.retention_anchor_at = timezone.now()
        application.save(update_fields=[
            "status", "reviewed_by", "reviewed_at", "rejection_reason",
            "retention_anchor_at", "updated_at",
        ])
        NotificationService.notify_legal_entity_rejected(application.user, application)
        messages.success(request, f"Заявка «{application.company_name}» отклонена.")
    else:
        messages.error(request, "Укажите причину отклонения.")
    return redirect("web:admin_legal_entities")


@_staff_required
@require_POST
def admin_legal_entity_ddocs_update(request, pk):
    """Вручную переключить статус обмена договором через «Ддокс»."""
    application = get_object_or_404(LegalEntityApplication, pk=pk)
    new_status = request.POST.get("ddocs_status")
    if new_status not in LegalEntityApplication.DdocsStatus.values:
        messages.error(request, "Некорректный статус Ддокс.")
        return redirect("web:admin_legal_entities")

    application.ddocs_status = new_status
    application.ddocs_note = request.POST.get("ddocs_note", "")
    if new_status == LegalEntityApplication.DdocsStatus.ACCESS_ISSUED:
        application.retention_anchor_at = timezone.now()
    application.save(update_fields=["ddocs_status", "ddocs_note", "retention_anchor_at", "updated_at"])
    messages.success(request, "Статус Ддокс обновлён.")
    return redirect("web:admin_legal_entities")


@_staff_required
@require_POST
def admin_legal_entity_issue_access(request, pk):
    """Сгенерировать пароль юрлицу и показать его один раз в интерфейсе сотрудника.

    Пароль никогда не отправляется по email/SMS — только для вставки в документ Ддокс.
    """
    application = get_object_or_404(
        LegalEntityApplication, pk=pk, status=LegalEntityApplication.Status.APPROVED,
    )
    raw_password = User.objects.make_random_password()
    user = application.user
    user.set_password(raw_password)
    user.status = User.Status.ACTIVE
    user.is_email_confirmed = True
    user.save(update_fields=["password", "status", "is_email_confirmed"])

    application.ddocs_status = LegalEntityApplication.DdocsStatus.ACCESS_ISSUED
    application.retention_anchor_at = timezone.now()
    application.save(update_fields=["ddocs_status", "retention_anchor_at", "updated_at"])

    return render(
        request, "admin_panel/legal_entities.html",
        {
            "applications": _legal_entity_queue(request.user),
            "ddocs_status_choices": LegalEntityApplication.DdocsStatus.choices,
            "issued_password": raw_password,
            "issued_for": application,
        },
    )


# ── Блогер: подтверждение личности (OneID) ───────────────────────────────────

def blogger_identity_submit(request):
    """Подтверждение личности блогера через OneID/ПИНФЛ, без email/пароля.

    Не создаёт User заранее — только IdentityVerification(PENDING), затем
    вызывает заглушку OneID. При успехе создаёт аккаунт блогера и запускает
    отправку логина/пароля по SMS (заглушка, ничего реально не отправляет).
    При неудаче — пользователь просто исправляет данные и пробует снова.
    """
    if request.user.is_authenticated:
        return redirect("web:landing" if request.user.role != User.Role.BLOGGER else "web:blogger_dashboard")

    form = BloggerIdentitySubmitForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        full_name = form.cleaned_data["full_name"]
        phone = form.cleaned_data["phone"]
        pinfl = form.cleaned_data["pinfl"]

        verification = IdentityVerification.objects.create(
            full_name=full_name, phone=phone, pinfl=pinfl,
        )
        result = get_oneid_backend().verify(full_name, phone, pinfl)

        if not result.success:
            verification.status = IdentityVerification.Status.FAILED
            verification.failure_reason = result.failure_reason
            verification.save(update_fields=["status", "failure_reason"])
            form.add_error(None, "Не удалось подтвердить личность. Проверьте данные и попробуйте снова.")
            return render(request, "registration/blogger_identity_submit.html", {"form": form})

        synthetic_email = f"blogger.{phone.lstrip('+')}@sms.internal"
        raw_password = User.objects.make_random_password()
        user = User.objects.create_user(
            email=synthetic_email,
            password=raw_password,
            role=User.Role.BLOGGER,
        )
        user.status = User.Status.ACTIVE
        user.is_email_confirmed = True
        user.save(update_fields=["status", "is_email_confirmed"])

        profile, _created = BloggerProfile.objects.get_or_create(user=user)
        profile.phone = phone
        profile.pinfl = pinfl
        profile.save(update_fields=["phone", "pinfl"])

        verification.user = user
        verification.status = IdentityVerification.Status.VERIFIED
        verification.provider_reference = result.reference
        verification.verified_at = timezone.now()
        verification.save(update_fields=["user", "status", "provider_reference", "verified_at"])

        send_blogger_sms_credentials.delay(user.pk, raw_password)

        messages.success(request, "Личность подтверждена, аккаунт создан. Логин и пароль отправлены на телефон.")
        return redirect("web:login")

    return render(request, "registration/blogger_identity_submit.html", {"form": form})


# ── Блогер: подтверждение статуса ИП ─────────────────────────────────────────

@login_required
def ip_application_list(request):
    if request.user.role != User.Role.BLOGGER:
        messages.error(request, "Доступно только блогерам.")
        return redirect("web:landing")
    applications = IPApplication.objects.filter(user=request.user).order_by("-created_at")
    return render(request, "registration/ip_application_list.html", {"applications": applications})


@login_required
def ip_application_upload(request):
    """Загрузить документ (патент/справка) на подтверждение статуса ИП.

    Требует уже подтверждённую личность через OneID (IdentityVerification.VERIFIED),
    привязанную к текущему пользователю.
    """
    if request.user.role != User.Role.BLOGGER:
        messages.error(request, "Доступно только блогерам.")
        return redirect("web:landing")

    verification = (
        IdentityVerification.objects.filter(user=request.user, status=IdentityVerification.Status.VERIFIED)
        .order_by("-verified_at")
        .first()
    )
    if not verification:
        messages.error(request, "Сначала нужно подтвердить личность через OneID.")
        return redirect("web:blogger_dashboard")

    form = IPApplicationForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        application = form.save(commit=False)
        application.user = request.user
        application.identity_verification = verification
        application.save()
        messages.success(request, "Документ загружен и отправлен на проверку.")
        return redirect("web:ip_application_list")

    return render(request, "registration/ip_application_upload.html", {"form": form})


@_staff_required
def admin_ip_applications(request):
    """Очередь заявок блогеров на подтверждение статуса ИП (PENDING)."""
    applications = (
        IPApplication.objects.filter(status=IPApplication.Status.PENDING)
        .select_related("user", "identity_verification")
        .order_by("created_at")
    )
    return render(request, "admin_panel/ip_applications.html", {"applications": applications})


@_staff_required
@require_POST
def admin_ip_application_approve(request, pk):
    application = get_object_or_404(IPApplication, pk=pk, status=IPApplication.Status.PENDING)
    IPApplicationStatusLog.log(application, IPApplication.Status.APPROVED, changed_by=request.user)
    application.status = IPApplication.Status.APPROVED
    application.reviewed_by = request.user
    application.reviewed_at = timezone.now()
    application.rejection_reason = ""
    application.retention_anchor_at = timezone.now()
    application.save(update_fields=[
        "status", "reviewed_by", "reviewed_at", "rejection_reason", "retention_anchor_at", "updated_at",
    ])

    profile = getattr(application.user, "blogger_profile", None)
    if profile:
        profile.is_ip_confirmed = True
        profile.save(update_fields=["is_ip_confirmed"])

    NotificationService.notify_ip_application_approved(application.user, application)
    messages.success(request, "Статус ИП подтверждён.")
    return redirect("web:admin_ip_applications")


@_staff_required
@require_POST
def admin_ip_application_reject(request, pk):
    application = get_object_or_404(IPApplication, pk=pk, status=IPApplication.Status.PENDING)
    form = AdminIPApplicationRejectForm(request.POST)
    if form.is_valid():
        IPApplicationStatusLog.log(
            application, IPApplication.Status.REJECTED,
            changed_by=request.user, comment=form.cleaned_data["rejection_reason"],
        )
        application.status = IPApplication.Status.REJECTED
        application.reviewed_by = request.user
        application.reviewed_at = timezone.now()
        application.rejection_reason = form.cleaned_data["rejection_reason"]
        application.retention_anchor_at = timezone.now()
        application.save(update_fields=[
            "status", "reviewed_by", "reviewed_at", "rejection_reason", "retention_anchor_at", "updated_at",
        ])
        NotificationService.notify_ip_application_rejected(application.user, application)
        messages.success(request, "Заявка отклонена.")
    else:
        messages.error(request, "Укажите причину отклонения.")
    return redirect("web:admin_ip_applications")
