"""Тестам: блогер по умолчанию — подтверждённый самозанятый (ПС 5.2: физлицо к сделкам не допускается).

Тестовый раннер (`config.test_runner.Runner`) включает `admit_bloggers_by_default`: новый профиль блогера с
категорией по умолчанию («физлицо») сохраняется как подтверждённый самозанятый — иначе ни один тест не дошёл бы
до отклика и сделки. Тест про физлицо или про неподтверждённый статус ставит его явно — `make_individual(user)`.
В рабочем коде проверка допуска не отключается: это только значение по умолчанию тестовых данных.
"""
from django.db.models.signals import pre_save

from .models import BloggerProfile


def _admit_new_profile(sender, instance, **kwargs):
    if instance._state.adding and instance.category == BloggerProfile.Category.INDIVIDUAL:
        instance.category = BloggerProfile.Category.SELF_EMPLOYED
        instance.is_self_employed_confirmed = True


def admit_bloggers_by_default():
    pre_save.connect(_admit_new_profile, sender=BloggerProfile, dispatch_uid="tests-admit-bloggers")


def make_individual(user):
    """Блогер без подтверждённого статуса — физлицо, как у нового пользователя на сайте."""
    BloggerProfile.objects.filter(user=user).update(
        category=BloggerProfile.Category.INDIVIDUAL, is_self_employed_confirmed=False,
        is_ip_confirmed=False, is_legal_entity_confirmed=False,
    )
    if hasattr(user, "blogger_profile"):
        user.blogger_profile.refresh_from_db()
    return user


class contracted_before_rule:
    """Сделка физлица, заключённая до правила ПС 5.2: на время заключения исполнитель допущен, потом категория
    возвращается как была. Для тестов выплат, ЭСФ и чеков, где исполнитель — физлицо со старой сделкой."""

    def __init__(self, user):
        self.user = user

    def __enter__(self):
        fields = ("category", "is_self_employed_confirmed", "is_ip_confirmed", "is_legal_entity_confirmed")
        self.saved = BloggerProfile.objects.filter(user=self.user).values(*fields).first()
        from .admission import is_admitted

        if self.saved and not is_admitted(self.user):
            BloggerProfile.objects.filter(user=self.user).update(
                category=BloggerProfile.Category.SELF_EMPLOYED, is_self_employed_confirmed=True,
            )
        else:
            self.saved = None
        return self.user

    def __exit__(self, *exc):
        if self.saved:
            BloggerProfile.objects.filter(user=self.user).update(**self.saved)
        return False
