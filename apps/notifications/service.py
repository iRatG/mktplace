"""
NotificationService — синхронный сервис создания in-app уведомлений (Модуль 11А).

Не использует Celery (на VPS Celery отключён). Создаёт Notification.objects.create()
напрямую в рамках текущего HTTP-запроса.

Паттерн использования:
    from apps.notifications.service import NotificationService
    NotificationService.notify_new_response(advertiser, campaign, blogger)

Все публичные методы:
    notify()                        — базовый метод, все параметры явно
    notify_new_response()           — новый отклик на кампанию → рекламодателю
    notify_response_accepted()      — отклик принят → блогеру
    notify_response_rejected()      — отклик отклонён → блогеру
    notify_direct_offer_received()  — прямое предложение получено → блогеру
    notify_direct_offer_accepted()  — прямое предложение принято → рекламодателю
    notify_direct_offer_rejected()  — прямое предложение отклонено → рекламодателю
    notify_deal_status_change()     — смена статуса сделки → обеим сторонам
    notify_deal_cancelled()         — сделка отменена → обеим сторонам
    notify_deal_completed()         — сделка завершена + деньги → блогеру
    notify_campaign_approved()      — кампания одобрена → рекламодателю
    notify_campaign_rejected()      — кампания отклонена → рекламодателю
    notify_platform_approved()      — площадка одобрена → блогеру
    notify_platform_rejected()      — площадка отклонена → блогеру
    notify_withdrawal_approved()    — вывод подтверждён → блогеру
    notify_withdrawal_rejected()    — вывод отклонён → блогеру
"""

from .models import Notification


class NotificationService:
    """Создаёт in-app уведомления для пользователей платформы.

    Все методы статические — не требуют инстанциирования.
    """

    @staticmethod
    def notify(user, notification_type, title, body, deal=None):
        """Базовый метод создания уведомления.

        Args:
            user (User):              получатель
            notification_type (str):  Notification.Type.*
            title (str):              заголовок (до 255 символов)
            body (str):               текст уведомления
            deal (Deal|None):         связанная сделка (если применимо)
        """
        try:
            Notification.objects.create(
                user=user,
                type=notification_type,
                title=title,
                body=body,
                related_deal=deal,
            )
        except Exception:
            # Уведомление не должно ломать основной флоу
            pass

    # ── Отклики ───────────────────────────────────────────────────────────────

    @staticmethod
    def notify_new_response(advertiser, campaign, blogger):
        """Новый отклик на кампанию → рекламодателю."""
        NotificationService.notify(
            user=advertiser,
            notification_type=Notification.Type.CAMPAIGN_RESPONSE,
            title="Новый отклик на кампанию",
            body=f"Блогер {blogger.email} откликнулся на кампанию «{campaign.name}».",
        )

    @staticmethod
    def notify_response_accepted(blogger, campaign, deal):
        """Отклик принят, сделка создана → блогеру."""
        NotificationService.notify(
            user=blogger,
            notification_type=Notification.Type.RESPONSE_ACCEPTED,
            title="Ваш отклик принят",
            body=(
                f"Рекламодатель принял ваш отклик на кампанию «{campaign.name}». "
                f"Сделка #{deal.pk} создана и деньги зарезервированы."
            ),
            deal=deal,
        )

    @staticmethod
    def notify_response_rejected(blogger, campaign):
        """Отклик отклонён → блогеру."""
        NotificationService.notify(
            user=blogger,
            notification_type=Notification.Type.RESPONSE_REJECTED,
            title="Отклик отклонён",
            body=f"Рекламодатель отклонил ваш отклик на кампанию «{campaign.name}».",
        )

    # ── Прямые предложения ────────────────────────────────────────────────────

    @staticmethod
    def notify_direct_offer_received(blogger, campaign, advertiser):
        """Рекламодатель отправил прямое предложение → блогеру."""
        NotificationService.notify(
            user=blogger,
            notification_type=Notification.Type.DIRECT_OFFER_RECEIVED,
            title="Новое предложение от рекламодателя",
            body=(
                f"Рекламодатель {advertiser.email} предлагает вам участие "
                f"в кампании «{campaign.name}». Проверьте входящие предложения."
            ),
        )

    @staticmethod
    def notify_direct_offer_accepted(advertiser, campaign, blogger, deal):
        """Блогер принял прямое предложение → рекламодателю."""
        NotificationService.notify(
            user=advertiser,
            notification_type=Notification.Type.DIRECT_OFFER_ACCEPTED,
            title="Предложение принято",
            body=(
                f"Блогер {blogger.email} принял ваше предложение по кампании "
                f"«{campaign.name}». Сделка #{deal.pk} создана."
            ),
            deal=deal,
        )

    @staticmethod
    def notify_direct_offer_rejected(advertiser, campaign, blogger):
        """Блогер отклонил прямое предложение → рекламодателю."""
        NotificationService.notify(
            user=advertiser,
            notification_type=Notification.Type.DIRECT_OFFER_REJECTED,
            title="Предложение отклонено",
            body=(
                f"Блогер {blogger.email} отклонил ваше предложение "
                f"по кампании «{campaign.name}»."
            ),
        )

    # ── Сделки ────────────────────────────────────────────────────────────────

    @staticmethod
    def notify_deal_completed(blogger, deal):
        """Сделка завершена, деньги зачислены → блогеру."""
        NotificationService.notify(
            user=blogger,
            notification_type=Notification.Type.PAYMENT_RECEIVED,
            title="Деньги зачислены на баланс",
            body=(
                f"Рекламодатель подтвердил сделку #{deal.pk} "
                f"«{deal.campaign.name}». Средства переведены на ваш баланс."
            ),
            deal=deal,
        )

    @staticmethod
    def notify_deal_cancelled(deal, cancelled_by):
        """Сделка отменена → обеим сторонам (кроме инициатора)."""
        other = deal.blogger if cancelled_by == deal.advertiser else deal.advertiser
        initiator_label = "Рекламодатель" if cancelled_by == deal.advertiser else "Блогер"
        NotificationService.notify(
            user=other,
            notification_type=Notification.Type.DEAL_CANCELLED,
            title="Сделка отменена",
            body=(
                f"{initiator_label} отменил сделку #{deal.pk} "
                f"«{deal.campaign.name}». Зарезервированные средства возвращены."
            ),
            deal=deal,
        )

    # ── Согласование креатива (Sprint 7) ──────────────────────────────────────

    @staticmethod
    def notify_creative_submitted(advertiser, deal):
        """Блогер отправил креатив на согласование → рекламодателю."""
        NotificationService.notify(
            user=advertiser,
            notification_type=Notification.Type.CREATIVE_SUBMITTED,
            title="Креатив на согласовании",
            body=(
                f"Блогер отправил креатив по сделке #{deal.pk} "
                f"«{deal.campaign.name}». Проверьте и согласуйте."
            ),
            deal=deal,
        )

    @staticmethod
    def notify_creative_approved(blogger, deal):
        """Рекламодатель согласовал креатив → блогеру."""
        NotificationService.notify(
            user=blogger,
            notification_type=Notification.Type.CREATIVE_APPROVED,
            title="Креатив согласован",
            body=(
                f"Рекламодатель согласовал ваш креатив по сделке #{deal.pk} "
                f"«{deal.campaign.name}». Можно публиковать!"
            ),
            deal=deal,
        )

    @staticmethod
    def notify_creative_rejected(blogger, deal):
        """Рекламодатель отклонил креатив → блогеру."""
        reason = deal.creative_rejection_reason or "причина не указана"
        NotificationService.notify(
            user=blogger,
            notification_type=Notification.Type.CREATIVE_REJECTED,
            title="Креатив отклонён",
            body=(
                f"Рекламодатель отклонил ваш креатив по сделке #{deal.pk} "
                f"«{deal.campaign.name}». Причина: {reason}"
            ),
            deal=deal,
        )

    # ── Кампании ──────────────────────────────────────────────────────────────

    @staticmethod
    def notify_campaign_approved(advertiser, campaign):
        """Кампания прошла модерацию → рекламодателю."""
        NotificationService.notify(
            user=advertiser,
            notification_type=Notification.Type.CAMPAIGN_STATUS,
            title="Кампания опубликована",
            body=f"Ваша кампания «{campaign.name}» прошла модерацию и теперь активна.",
        )

    @staticmethod
    def notify_campaign_rejected(advertiser, campaign):
        """Кампания отклонена модератором → рекламодателю."""
        reason = campaign.rejection_reason or "причина не указана"
        NotificationService.notify(
            user=advertiser,
            notification_type=Notification.Type.CAMPAIGN_STATUS,
            title="Кампания отклонена",
            body=f"Кампания «{campaign.name}» отклонена модератором. Причина: {reason}",
        )

    # ── Площадки ──────────────────────────────────────────────────────────────

    @staticmethod
    def notify_platform_approved(blogger, platform):
        """Площадка одобрена → блогеру."""
        NotificationService.notify(
            user=blogger,
            notification_type=Notification.Type.PLATFORM_MODERATED,
            title="Площадка одобрена",
            body=(
                f"Ваша площадка {platform.get_social_type_display()} "
                f"({platform.url}) прошла проверку и теперь видна рекламодателям."
            ),
        )

    @staticmethod
    def notify_platform_rejected(blogger, platform):
        """Площадка отклонена → блогеру."""
        reason = platform.rejection_reason or "причина не указана"
        NotificationService.notify(
            user=blogger,
            notification_type=Notification.Type.PLATFORM_MODERATED,
            title="Площадка отклонена",
            body=(
                f"Ваша площадка {platform.get_social_type_display()} "
                f"({platform.url}) отклонена. Причина: {reason}"
            ),
        )

    # ── Вывод средств ─────────────────────────────────────────────────────────

    @staticmethod
    def notify_withdrawal_approved(blogger, amount):
        """Заявка на вывод одобрена → блогеру."""
        NotificationService.notify(
            user=blogger,
            notification_type=Notification.Type.WITHDRAWAL_APPROVED,
            title="Выплата подтверждена",
            body=f"Ваша заявка на вывод {amount:,.0f} одобрена и обработана.",
        )

    @staticmethod
    def notify_withdrawal_rejected(blogger, amount, comment=""):
        """Заявка на вывод отклонена → блогеру."""
        reason = f" Причина: {comment}" if comment else ""
        NotificationService.notify(
            user=blogger,
            notification_type=Notification.Type.WITHDRAWAL_REJECTED,
            title="Заявка на вывод отклонена",
            body=f"Ваша заявка на вывод {amount:,.0f} отклонена.{reason} Средства возвращены на баланс.",
        )
