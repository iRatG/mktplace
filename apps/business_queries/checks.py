"""Django system checks для модуля внутренних тикетов ИТ.

Симметрично apps.registration.checks: если группа "IT Team" пуста, никто
не сможет открыть /tickets/ (гейт `_it_team_required`) и Django admin для
Ticket тоже откажет всем (`_is_it_team_member`, см. CRIT-5 в
AUDIT_REPORT_2026-09-13.md) — тикеты продолжат создаваться, но их некому
будет прочитать. Проверка добавлена по итогам того же разбора, что и
apps.registration.checks (openspec change
2026-09-20-fix-registration-reviewer-and-deploy-gaps).
"""
from django.core.checks import Tags, Warning, register


@register(Tags.database)
def check_it_team_group(app_configs, **kwargs):
    """Предупреждает, если группу "IT Team" некому читать."""
    from django.db.utils import OperationalError, ProgrammingError

    from apps.users.models import User

    try:
        has_members = User.objects.filter(
            is_staff=True, is_active=True, groups__name="IT Team",
        ).exists()
    except (OperationalError, ProgrammingError):
        return []

    if has_members:
        return []

    return [
        Warning(
            'Группа "IT Team" пуста — /tickets/ и Django admin для тикетов '
            "недоступны никому, хотя тикеты продолжают создаваться.",
            hint=(
                "python manage.py shell -c \"from django.contrib.auth.models "
                "import Group; from apps.users.models import User; "
                "User.objects.get(email='...').groups.add("
                "Group.objects.get(name='IT Team'))\""
            ),
            id="apps.business_queries.W001",
        )
    ]
