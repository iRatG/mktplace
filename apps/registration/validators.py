"""Проверка ИНН юрлица — одно правило для модели, форм, Django admin и выдачи доступа."""
import re

from django.core.exceptions import ValidationError

INN_RE = re.compile(r"\d{9}")
INN_ERROR = "ИНН должен состоять ровно из 9 цифр."


def is_valid_inn(value):
    return bool(INN_RE.fullmatch(value or ""))


def validate_inn(value):
    if not is_valid_inn(value):
        raise ValidationError(INN_ERROR, code="invalid_inn")
