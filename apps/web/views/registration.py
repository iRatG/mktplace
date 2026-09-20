"""Views модуля регистрации юрлиц и блогеров (ИП).

Рекламодатель (юрлицо):
    /register/legal-entity/            — подать заявку (название + ИНН), точка входа с нуля
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
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.notifications.service import NotificationService
from apps.profiles.models import AdvertiserProfile, BloggerProfile
from apps.registration.models import (
    IdentityVerification,
    IPApplication,
    IPApplicationStatusLog,
    LegalEntityApplication,
    LegalEntityApplicationStatusLog,
)
from apps.registration.services import REGISTRATION_REVIEWERS_GROUP, assign_reviewer, get_oneid_backend
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

def legal_entity_submit(request):
    """Точка входа юрлица с нуля: только название + ИНН, без email/пароля.

    По макету бизнеса (task/bloger 12092026) аккаунта в этот момент ещё нет —
    он появится позже, когда сотрудник выдаст доступ после обмена договором
    через «Ддокс» (см. admin_legal_entity_issue_access). Здесь заявка просто
    создаётся и автоматически закрепляется за сотрудником.
    """
    if request.user.is_authenticated:
        return redirect("web:landing" if request.user.role != User.Role.ADVERTISER else "web:advertiser_dashboard")

    form = LegalEntityApplicationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        application = form.save(commit=False)
        application.save()

        reviewer = assign_reviewer()
        if reviewer:
            application.assigned_to = reviewer
            application.save(update_fields=["assigned_to"])
            NotificationService.notify_legal_entity_assigned(reviewer, application)

        return render(request, "registration/legal_entity_submit.html", {"form": LegalEntityApplicationForm(), "submitted": True})

    return render(request, "registration/legal_entity_submit.html", {"form": form})


def _legal_entity_queue(user):
    """Очередь заявок юрлиц, закреплённых за сотрудником.

    Включает PENDING (нужно одобрить/отклонить) и APPROVED, доступ по которым
    ещё не выдан (нужно довести обмен через «Ддокс» до конца) — заявка не
    исчезает из личной очереди сотрудника, пока доступ не выдан.

    Персональное закрепление (assigned_to) — по требованию бизнеса, не общая
    очередь. Но если assign_reviewer() не нашёл ревьюера на момент подачи
    (например в группе "Регистрация юрлиц" временно никого не было),
    assigned_to остаётся NULL навсегда — такую неназначенную заявку не должен
    видеть никто. Поэтому любому участнику этой группы дополнительно
    показываем неназначенные заявки, чтобы они не терялись молча.
    """
    scope = Q(assigned_to=user)
    if user.groups.filter(name=REGISTRATION_REVIEWERS_GROUP).exists():
        scope |= Q(assigned_to__isnull=True)

    return (
        LegalEntityApplication.objects.filter(scope)
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
    if application.user:
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
        if application.user:
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
    """Создать (при первой выдаче) или обновить пароль аккаунта юрлица.

    Аккаунт для юрлица не существует до этого момента — заявка подавалась
    без email/пароля (см. legal_entity_submit). Логин генерируется здесь же,
    показывается сотруднику ровно один раз для вставки в документ «Ддокс».
    Пароль никогда не отправляется по email/SMS.
    """
    application = get_object_or_404(
        LegalEntityApplication, pk=pk, status=LegalEntityApplication.Status.APPROVED,
    )
    raw_password = User.objects.make_random_password()

    if application.user:
        user = application.user
    else:
        # get_or_create, не create_user: тот же ИНН может уже иметь аккаунт
        # (повторная выдача доступа, либо заявка была пересоздана после
        # удаления старой — юрлицо не должно упереться в IntegrityError
        # из-за детерминированного логина legal.<инн>@ddocs.internal).
        login = f"legal.{application.inn}@ddocs.internal"
        user, _created = User.objects.get_or_create(
            email=login, defaults={"role": User.Role.ADVERTISER},
        )
        application.user = user

    user.set_password(raw_password)
    user.status = User.Status.ACTIVE
    user.is_email_confirmed = True
    user.save(update_fields=["password", "status", "is_email_confirmed"])

    profile, _created = AdvertiserProfile.objects.get_or_create(user=user)
    profile.company_name = application.company_name
    profile.inn = application.inn
    profile.save(update_fields=["company_name", "inn"])

    application.ddocs_status = LegalEntityApplication.DdocsStatus.ACCESS_ISSUED
    application.retention_anchor_at = timezone.now()
    application.save(update_fields=["user", "ddocs_status", "retention_anchor_at", "updated_at"])

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

        # get_or_create, не create_user: тот же телефон может уже иметь
        # аккаунт (повторная попытка OneID — SMS потерялось, опечатка в
        # прошлый раз и т.п.) — тот же класс бага, что и с ИНН юрлица,
        # см. fix-registration-entry-points.
        synthetic_email = f"blogger.{phone.lstrip('+')}@sms.internal"
        raw_password = User.objects.make_random_password()
        user, _created = User.objects.get_or_create(
            email=synthetic_email, defaults={"role": User.Role.BLOGGER},
        )
        user.set_password(raw_password)
        user.status = User.Status.ACTIVE
        user.is_email_confirmed = True
        user.save(update_fields=["password", "status", "is_email_confirmed"])

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
