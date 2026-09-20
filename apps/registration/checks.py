"""Django system checks для модуля регистрации.

Появились после инцидента 19-20.09.2026 (openspec change
2026-09-20-fix-registration-reviewer-and-deploy-gaps): группа
"Регистрация юрлиц" была пуста на проде, assign_reviewer() всегда
возвращал None, и заявки юрлиц не показывались ни одному сотруднику —
никто об этом не узнал, пока тестировщик не столкнулся с этим вручную.
Эта проверка делает такую ситуацию видимой сразу при старте/миграции,
а не только когда кто-то случайно заметит пустую очередь.
"""
from django.core.checks import Tags, Warning, register


@register(Tags.database)
def check_registration_reviewer_pool(app_configs, **kwargs):
    """Предупреждает, если некому автоматически назначать заявки юрлиц."""
    from django.db.utils import OperationalError, ProgrammingError

    from apps.users.models import User

    from .services import REGISTRATION_REVIEWERS_GROUP

    try:
        has_reviewers = User.objects.filter(
            is_staff=True, is_active=True, groups__name=REGISTRATION_REVIEWERS_GROUP,
        ).exists()
    except (OperationalError, ProgrammingError):
        # БД ещё не готова/не смигрирована (например при сборке образа) —
        # не мешаем той команде, которая сейчас выполняется.
        return []

    if has_reviewers:
        return []

    return [
        Warning(
            f'Группа "{REGISTRATION_REVIEWERS_GROUP}" пуста — новые заявки '
            "юрлиц не будут автоматически закреплены ни за кем "
            "(assign_reviewer() вернёт None). Очередь /panel/legal-entities/ "
            "покажет их только участникам этой группы как неназначенные.",
            hint=(
                "Добавьте активного staff-пользователя в группу, например: "
                "python manage.py shell -c \"from django.contrib.auth.models "
                "import Group; from apps.users.models import User; "
                "User.objects.get(email='...').groups.add("
                "Group.objects.get(name='Регистрация юрлиц'))\""
            ),
            id="apps.registration.W001",
        )
    ]
