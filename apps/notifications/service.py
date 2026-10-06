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
    notify_publication_submitted()  — публикация добавлена → рекламодателю
    notify_campaign_moderation_requested() — кампания на модерации → всем активным staff

Уведомление гаснет само, когда адресат открывает страницу, на которую оно ведёт
(apps/notifications/middleware.py) — во views гасить не нужно.
"""

from django.urls import reverse

from apps.billing.formatting import format_money

from .models import Notification


class NotificationService:
    """Создаёт in-app уведомления для пользователей платформы.

    Все методы статические — не требуют инстанциирования.
    """

    @staticmethod
    def notify(user, notification_type, title, body, deal=None, url=""):
        """Базовый метод создания уведомления.

        Args:
            user (User):              получатель
            notification_type (str):  Notification.Type.*
            title (str):              заголовок (до 255 символов)
            body (str):               текст уведомления
            deal (Deal|None):         связанная сделка (если применимо)
            url (str):                куда вести по клику на уведомление (относительный путь)
        """
        try:
            Notification.objects.create(
                user=user,
                type=notification_type,
                title=title,
                body=body,
                related_deal=deal,
                url=url,
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
            url=reverse("web:campaign_detail", kwargs={"pk": campaign.pk}),
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
    def notify_response_rejected(blogger, campaign, reason=""):
        """Отклик отклонён → блогеру (с комментарием рекламодателя, если он есть)."""
        body = f"Рекламодатель отклонил ваш отклик на кампанию «{campaign.name}»."
        if reason:
            body += f" Комментарий: {reason}"
        body += " Вы можете откликнуться снова."
        NotificationService.notify(
            user=blogger,
            notification_type=Notification.Type.RESPONSE_REJECTED,
            title="Отклик отклонён",
            body=body,
            url=reverse("web:my_responses"),
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
            url=reverse("web:blogger_dashboard"),
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

    @staticmethod
    def notify_dispute_opened(deal, opened_by):
        """Открыт спор → другой стороне и всем активным сотрудникам."""
        from apps.users.models import User

        other = deal.blogger if opened_by == deal.advertiser else deal.advertiser
        who = "Рекламодатель" if opened_by == deal.advertiser else "Блогер"
        NotificationService.notify(
            user=other,
            notification_type=Notification.Type.DEAL_DISPUTED,
            title="Открыт спор по сделке",
            body=(f"{who} открыл спор по сделке #{deal.pk} «{deal.campaign.name}». Причина: {deal.dispute_reason}. "
                  f"Деньги заморожены до решения сотрудника."),
            deal=deal,
        )
        for staff in User.objects.filter(is_staff=True, is_active=True):
            NotificationService.notify(
                user=staff,
                notification_type=Notification.Type.DEAL_DISPUTED,
                title="Новый спор",
                body=f"Спор по сделке #{deal.pk} «{deal.campaign.name}»: {deal.dispute_reason}",
                url=reverse("web:admin_disputes"),
            )

    @staticmethod
    def notify_dispute_resolved(deal):
        """Спор разрешён сотрудником → обеим сторонам."""
        paid = deal.status == "completed"
        result = "оплата переведена блогеру" if paid else "средства возвращены рекламодателю"
        for user in (deal.blogger, deal.advertiser):
            NotificationService.notify(
                user=user,
                notification_type=Notification.Type.DEAL_UPDATED,
                title="Спор разрешён",
                body=f"По сделке #{deal.pk} «{deal.campaign.name}» принято решение: {result}.",
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
    def notify_publication_submitted(deal):
        """Блогер добавил публикацию → рекламодателю (подтвердить или открыть спор)."""
        NotificationService.notify(
            user=deal.advertiser,
            notification_type=Notification.Type.PUBLICATION_SUBMITTED,
            title="Блогер добавил публикацию",
            body=(
                f"Блогер разместил публикацию по сделке #{deal.pk} «{deal.campaign.name}». "
                f"Проверьте и подтвердите — без ответа сделка завершится автоматически через 72 часа."
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
            url=reverse("web:campaign_detail", kwargs={"pk": campaign.pk}),
        )

    @staticmethod
    def notify_campaign_completed(campaign):
        """Срок кампании истёк, она завершена → рекламодателю."""
        NotificationService.notify(
            user=campaign.advertiser,
            notification_type=Notification.Type.CAMPAIGN_STATUS,
            title="Кампания завершена",
            body=(
                f"Срок кампании «{campaign.name}» истёк — она завершена. Ожидавшие решения отклики и предложения "
                f"закрыты, начатые сделки продолжаются."
            ),
            url=reverse("web:campaign_detail", kwargs={"pk": campaign.pk}),
        )

    @staticmethod
    def notify_response_expired(blogger, campaign):
        """Кампания завершилась, пока отклик ждал решения → блогеру."""
        NotificationService.notify(
            user=blogger,
            notification_type=Notification.Type.CAMPAIGN_STATUS,
            title="Кампания завершилась",
            body=f"Кампания «{campaign.name}» завершилась, ваш отклик закрыт без решения.",
            url=reverse("web:my_responses"),
        )

    @staticmethod
    def notify_direct_offer_expired(blogger, campaign):
        """Кампания завершилась, пока прямое предложение ждало ответа → блогеру."""
        NotificationService.notify(
            user=blogger,
            notification_type=Notification.Type.CAMPAIGN_STATUS,
            title="Предложение больше не действует",
            body=f"Кампания «{campaign.name}» завершилась — предложение по ней закрыто.",
            url=reverse("web:blogger_dashboard"),
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
            url=reverse("web:campaign_detail", kwargs={"pk": campaign.pk}),
        )

    # Откуда кампания пришла на модерацию — для заголовка уведомления сотрудникам.
    MODERATION_FIRST = "first"
    MODERATION_AFTER_REJECTION = "after_rejection"
    MODERATION_AFTER_PAUSE_EDIT = "after_pause_edit"

    @staticmethod
    def notify_campaign_moderation_requested(campaign, source):
        """Кампания пришла на модерацию → всем активным сотрудникам."""
        from apps.users.models import User

        titles = {
            NotificationService.MODERATION_FIRST: "Новая кампания на модерации",
            NotificationService.MODERATION_AFTER_REJECTION: "Кампания повторно на модерации после отклонения",
            NotificationService.MODERATION_AFTER_PAUSE_EDIT: "Кампания на модерации после правки на паузе",
        }
        title = titles.get(source, titles[NotificationService.MODERATION_FIRST])
        url = reverse("web:admin_campaign_detail", kwargs={"pk": campaign.pk})
        for staff in User.objects.filter(is_staff=True, is_active=True):
            NotificationService.notify(
                user=staff,
                notification_type=Notification.Type.CAMPAIGN_MODERATION_REQUESTED,
                title=title,
                body=f"Кампания «{campaign.name}» ({campaign.advertiser.email}) ждёт проверки.",
                url=url,
            )

    @staticmethod
    def notify_campaign_changes_proposed(advertiser, campaign):
        """Модератор предложил правки кампании → рекламодателю."""
        NotificationService.notify(
            user=advertiser,
            notification_type=Notification.Type.CAMPAIGN_CHANGES_PROPOSED,
            title="Модератор предложил правки",
            body=f"Модератор предложил исправления в кампании «{campaign.name}». Примите их или отклоните.",
            url=reverse("web:campaign_detail", kwargs={"pk": campaign.pk}),
        )

    @staticmethod
    def notify_campaign_changes_answered(moderator, campaign, accepted):
        """Рекламодатель принял или отклонил правки → автору-модератору."""
        if moderator is None:
            return
        NotificationService.notify(
            user=moderator,
            notification_type=(
                Notification.Type.CAMPAIGN_CHANGES_ACCEPTED if accepted
                else Notification.Type.CAMPAIGN_CHANGES_DECLINED
            ),
            title="Правки приняты" if accepted else "Правки отклонены",
            body=(
                f"Рекламодатель принял ваши правки — кампания «{campaign.name}» активна."
                if accepted else
                f"Рекламодатель отклонил ваши правки к кампании «{campaign.name}» и исправит её сам."
            ),
            url=reverse("web:admin_campaign_detail", kwargs={"pk": campaign.pk}),
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
            url=reverse("web:profile"),
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
            url=reverse("web:profile"),
        )

    # ── Вывод средств ─────────────────────────────────────────────────────────

    @staticmethod
    def notify_withdrawal_approved(blogger, amount):
        """Заявка на вывод одобрена → блогеру."""
        NotificationService.notify(
            user=blogger,
            notification_type=Notification.Type.WITHDRAWAL_APPROVED,
            title="Выплата подтверждена",
            body=f"Ваша заявка на вывод {format_money(amount)} одобрена и обработана.",
            url=reverse("web:wallet"),
        )

    @staticmethod
    def notify_withdrawal_rejected(blogger, amount, comment=""):
        """Заявка на вывод отклонена → блогеру."""
        reason = f" Причина: {comment}" if comment else ""
        NotificationService.notify(
            user=blogger,
            notification_type=Notification.Type.WITHDRAWAL_REJECTED,
            title="Заявка на вывод отклонена",
            body=f"Ваша заявка на вывод {format_money(amount)} отклонена.{reason} Средства возвращены на баланс.",
            url=reverse("web:wallet"),
        )

    # ── Регистрация юрлиц ─────────────────────────────────────────────────────

    @staticmethod
    def notify_legal_entity_assigned(staff, application):
        """Заявка юрлица закреплена за сотрудником → сотруднику."""
        NotificationService.notify(
            user=staff,
            notification_type=Notification.Type.LEGAL_ENTITY_ASSIGNED,
            title="Новая заявка юрлица на проверку",
            body=(
                f"За вами закреплена заявка «{application.company_name}» "
                f"(ИНН {application.inn}) на регистрацию."
            ),
            url=reverse("web:admin_legal_entity_detail", kwargs={"pk": application.pk}),
        )

    @staticmethod
    def notify_legal_entity_approved(advertiser, application):
        """Заявка юрлица подтверждена → рекламодателю."""
        NotificationService.notify(
            user=advertiser,
            notification_type=Notification.Type.LEGAL_ENTITY_APPROVED,
            title="Заявка юрлица подтверждена",
            body=f"Регистрация «{application.company_name}» подтверждена.",
            url=reverse("web:advertiser_dashboard"),
        )

    @staticmethod
    def notify_legal_entity_rejected(advertiser, application):
        """Заявка юрлица отклонена → рекламодателю."""
        reason = application.rejection_reason or "причина не указана"
        NotificationService.notify(
            user=advertiser,
            notification_type=Notification.Type.LEGAL_ENTITY_REJECTED,
            title="Заявка юрлица отклонена",
            body=f"Регистрация «{application.company_name}» отклонена. Причина: {reason}",
            url=reverse("web:advertiser_dashboard"),
        )

    # ── Подтверждение статуса ИП ─────────────────────────────────────────────

    @staticmethod
    def notify_ip_application_approved(blogger, application):
        """Заявка на статус ИП подтверждена → блогеру."""
        NotificationService.notify(
            user=blogger,
            notification_type=Notification.Type.IP_APPLICATION_APPROVED,
            title="Статус ИП подтверждён",
            body="Ваш документ (патент/справка) проверен, статус ИП подтверждён.",
            url=reverse("web:ip_application_list"),
        )

    @staticmethod
    def notify_ip_application_rejected(blogger, application):
        """Заявка на статус ИП отклонена → блогеру."""
        reason = application.rejection_reason or "причина не указана"
        NotificationService.notify(
            user=blogger,
            notification_type=Notification.Type.IP_APPLICATION_REJECTED,
            title="Заявка на статус ИП отклонена",
            body=f"Ваш документ отклонён. Причина: {reason}",
            url=reverse("web:ip_application_list"),
        )
