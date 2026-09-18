"""Сервисы модуля регистрации юрлиц и блогеров.

Содержит:
- назначение заявки юрлица на сотрудника (round-robin, apps.registration
  REGISTRATION_REVIEWERS_GROUP);
- заглушки внешних сервисов, договорённостей с которыми пока нет:
  OneID/MyID (подтверждение личности блогера) и SMS-провайдер (доставка
  логина/пароля). Обе заглушки — точки расширения: когда появится реальный
  доступ, меняется только реализация get_oneid_backend()/get_sms_backend(),
  вызывающий код не трогаем.

Бизнес прямо попросил (task/bloger 12092026/ответ_18-09-2026.txt, Q12 +
уточнение в чате 18.09.2026): реальные интеграции сейчас не подключаем.
Для SMS отдельно уточнено — метод заложить в код, но ничего никому реально
не отправлять, пока нет бюджета на провайдера.
"""
import logging
import uuid
from dataclasses import dataclass

from django.db.models import Count, Q

logger = logging.getLogger(__name__)

REGISTRATION_REVIEWERS_GROUP = "Регистрация юрлиц"


def assign_reviewer():
    """Вернуть сотрудника с наименьшим числом открытых заявок юрлиц.

    Пул — участники группы REGISTRATION_REVIEWERS_GROUP (менеджеры разбирают
    заявки по совместительству, выделенной роли нет — см. Q2/Q13). Если пул
    пуст, возвращает None — заявка остаётся неназначенной, сотрудник может
    закрепить её за собой вручную.
    """
    from django.contrib.auth import get_user_model
    from .models import LegalEntityApplication

    User = get_user_model()
    candidates = (
        User.objects.filter(
            is_staff=True,
            is_active=True,
            groups__name=REGISTRATION_REVIEWERS_GROUP,
        )
        .annotate(
            open_count=Count(
                "assigned_legal_entity_applications",
                filter=Q(
                    assigned_legal_entity_applications__status=LegalEntityApplication.Status.PENDING
                ),
            )
        )
        .order_by("open_count", "id")
    )
    return candidates.first()


@dataclass
class OneIDResult:
    success: bool
    reference: str = ""
    failure_reason: str = ""


class OneIDVerificationBackend:
    """Точка расширения для реальной интеграции с id.egov.uz / MyID."""

    def verify(self, full_name: str, phone: str, pinfl: str) -> OneIDResult:
        raise NotImplementedError


class StubOneIDVerificationBackend(OneIDVerificationBackend):
    """Заглушка: всегда подтверждает личность. Доступа к реальному OneID ещё нет."""

    def verify(self, full_name: str, phone: str, pinfl: str) -> OneIDResult:
        logger.info("OneID stub: verifying %s / %s / %s", full_name, phone, pinfl)
        return OneIDResult(success=True, reference=f"stub-{uuid.uuid4().hex[:10]}")


def get_oneid_backend() -> OneIDVerificationBackend:
    return StubOneIDVerificationBackend()


class SmsBackend:
    """Точка расширения для реального SMS-провайдера."""

    def send(self, phone: str, message: str) -> bool:
        raise NotImplementedError


class LogSmsBackend(SmsBackend):
    """Заглушка: только логирует, ничего реально не отправляет.

    Провайдер и бюджет на оплату SMS ещё не согласованы — по прямому
    указанию бизнеса на этом этапе отправка должна быть полностью
    имитационной, без реальных вызовов внешнего API.
    """

    def send(self, phone: str, message: str) -> bool:
        logger.info("SMS stub -> %s: %s", phone, message)
        return True


def get_sms_backend() -> SmsBackend:
    return LogSmsBackend()
