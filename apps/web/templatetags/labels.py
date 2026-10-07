"""Подписи и даты по-русски (QA camp_test_3): коды форматов и соцсетей — подписями, даты кампании — словами.

- `long_date` — «08 октября 2026» (месяц в родительном падеже, LANGUAGE_CODE=ru). Для дат кампании и окна
  приёма контента: цифры «08.10.2026» сливаются (за этим следит тест `tests_labels_and_dates`).
- `content_type_label`, `social_label` — подпись из общего списка в `apps/campaigns/models.py`, неизвестный код
  выводится как есть.
"""
from django import template
from django.template.defaultfilters import date as date_filter

from apps.campaigns.models import CONTENT_TYPE_CHOICES, SOCIAL_CHOICES

register = template.Library()

_CONTENT_TYPES = dict(CONTENT_TYPE_CHOICES)
_SOCIALS = dict(SOCIAL_CHOICES)


@register.filter
def long_date(value):
    """date(2026, 10, 8) → «08 октября 2026»; пустое значение → ''."""
    if not value:
        return ""
    return date_filter(value, "d E Y")


@register.filter
def content_type_label(code):
    return _CONTENT_TYPES.get(code, code)


@register.filter
def social_label(code):
    return _SOCIALS.get(code, code)
