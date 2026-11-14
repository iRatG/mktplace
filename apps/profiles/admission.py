"""Допуск исполнителя к сделкам (ПС 5.2, 5.3, редакция 07.11.2026).

Исполнителем по Сделке может быть только юрлицо, ИП или самозанятый с подтверждённым статусом; физлица
не допускаются. Одна проверка для отклика (сайт и API), направления оферты (по отклику и прямой), Акцепта,
каталога и приглашений. Сделки, заключённые до правила, доживают как есть — проверка стоит только на входах.
"""
from django.db.models import Q

from .models import BloggerProfile

NOT_ADMITTED_MESSAGE = (
    "Откликаться и заключать сделки могут только исполнители с подтверждённым статусом ИП, самозанятого "
    "или юрлица. Подтвердите статус в профиле."
)
NOT_ADMITTED_FOR_ADVERTISER = (
    "Этот исполнитель ещё не подтвердил статус ИП, самозанятого или юрлица — направить ему оферту нельзя."
)

# Категория → поле подтверждения в профиле.
CONFIRMED_FIELD = {
    BloggerProfile.Category.IP: "is_ip_confirmed",
    BloggerProfile.Category.SELF_EMPLOYED: "is_self_employed_confirmed",
    BloggerProfile.Category.LEGAL_ENTITY: "is_legal_entity_confirmed",
}


def profile_admitted(profile):
    """Профиль исполнителя допущен: выбранная категория — не физлицо и подтверждена документом."""
    field = CONFIRMED_FIELD.get(getattr(profile, "category", None))
    return bool(field and getattr(profile, field))


def is_admitted(user):
    """Пользователь — исполнитель с подтверждённым статусом."""
    profile = BloggerProfile.objects.filter(user_id=getattr(user, "pk", None)).first()
    return profile is not None and profile_admitted(profile)


def admitted_q(prefix=""):
    """Q-фильтр «допущенный исполнитель» для запросов; prefix — путь до пользователя, например ``"blogger__"``."""
    path = f"{prefix}blogger_profile__"
    q = Q()
    for category, field in CONFIRMED_FIELD.items():
        q |= Q(**{f"{path}category": category, f"{path}{field}": True})
    return q
