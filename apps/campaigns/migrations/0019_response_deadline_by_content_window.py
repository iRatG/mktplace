"""Ожидающие отклики получают срок по правилу 10.10.2026: конец дня «окончание приёма контента − 2 рабочих дня».

Логика скопирована из apps.campaigns.validation.response_deadline_for: в миграции — исторические модели.
У кампании без даты окончания срок не меняется (прежние 7 дней).
"""
from datetime import datetime, time, timedelta

from django.db import migrations
from django.utils import timezone


def _working_days_before(day, count):
    current = day
    while count > 0:
        current -= timedelta(days=1)
        if current.weekday() < 5:
            count -= 1
    return current


def forwards(apps, schema_editor):
    Response = apps.get_model("campaigns", "Response")
    for resp in Response.objects.filter(status="pending").select_related("campaign"):
        campaign = resp.campaign
        content_end = campaign.deadline or (
            _working_days_before(campaign.end_date, 5) if campaign.end_date else None
        )
        if content_end is None:
            continue
        day = _working_days_before(content_end, 2)
        deadline = timezone.make_aware(datetime.combine(day, time(23, 59, 59)))
        if deadline != resp.expires_at:
            resp.expires_at = deadline
            resp.reminder_sent_at = None
            resp.save(update_fields=["expires_at", "reminder_sent_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("campaigns", "0018_closed_campaigns"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
