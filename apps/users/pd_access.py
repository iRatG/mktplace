"""Журнал доступа сотрудников к персональным данным (PersonalDataAccess).

Вызывать там, где сотрудник открывает или выгружает чужие данные: файлы документов, карточку и реестр юрлиц, очереди
документов исполнителей и реквизитов выплаты. Сторона сделки, открывающая материалы своей сделки, в журнал не пишется.
"""
from .models import PersonalDataAccess
from .security import client_ip


def log_access(request, action, subject=None):
    """Записать доступ сотрудника; для не-сотрудников и для своих же данных — ничего не делает."""
    user = request.user
    if not (user.is_authenticated and user.is_staff):
        return None
    if subject is not None and subject == user:
        return None
    return PersonalDataAccess.objects.create(
        staff=user, subject=subject, action=action[:255], path=request.get_full_path()[:500],
        ip=client_ip(request) or None,
    )
