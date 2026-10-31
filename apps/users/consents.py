"""Согласия пользователей: условия и обработка персональных данных, рассылки, показ профиля в каталоге.

Журнал `Consent` только дополняется: действующее значение — последняя запись этого вида. Редакция документов —
настройка `LEGAL_EDITION`; пока она пуста, окно принятия новой редакции выключено.
"""
from django.conf import settings

from .models import Consent, User
from .security import client_ip


def record(kind, granted=True, *, request=None, user=None, legal_application=None):
    return Consent.objects.create(
        user=user, legal_application=legal_application, kind=kind, granted=bool(granted),
        edition=settings.LEGAL_EDITION, ip=(client_ip(request) or None) if request else None,
    )


def record_registration(cleaned, request, *, user=None, legal_application=None):
    """Согласия из формы регистрации. Заявка юрлица приходит без аккаунта — записи привязываются к заявке."""
    record(Consent.Kind.TERMS, request=request, user=user, legal_application=legal_application)
    record(Consent.Kind.MARKETING, cleaned.get("accept_marketing"), request=request, user=user,
           legal_application=legal_application)
    if "show_in_catalog" in cleaned:
        set_catalog(user, cleaned["show_in_catalog"], request)


def attach_to_user(application, user):
    """Выдан доступ по заявке: согласия из заявки становятся согласиями аккаунта."""
    Consent.objects.filter(legal_application=application, user__isnull=True).update(user=user)


def is_granted(user, kind):
    latest = Consent.objects.filter(user=user, kind=kind).order_by("-created_at", "-pk").first()
    return bool(latest and latest.granted)


def set_marketing(user, granted, request):
    """Изменить согласие на рассылки; без изменения журнал не пополняется."""
    if is_granted(user, Consent.Kind.MARKETING) != bool(granted):
        record(Consent.Kind.MARKETING, granted, request=request, user=user)


def set_catalog(user, shown, request):
    """Показ профиля в каталоге: поле профиля для отбора и запись в журнал."""
    profile = user.blogger_profile
    if profile.show_in_catalog != bool(shown) or not Consent.objects.filter(user=user, kind=Consent.Kind.CATALOG).exists():
        profile.show_in_catalog = bool(shown)
        profile.save(update_fields=["show_in_catalog"])
        record(Consent.Kind.CATALOG, shown, request=request, user=user)


def needs_acceptance(user):
    """Нужно ли пользователю принять действующую редакцию документов."""
    edition = settings.LEGAL_EDITION
    if not edition or user.is_staff or user.role not in User.Role.values:
        return False
    return not Consent.objects.filter(user=user, kind=Consent.Kind.TERMS, granted=True, edition=edition).exists()
