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
from django.utils import timezone

from apps.billing.formatting import format_money

from .models import Notification


class NotificationService:
    """Создаёт in-app уведомления для пользователей платформы.

    Все методы статические — не требуют инстанциирования.
    """

    @staticmethod
    def notify(user, notification_type, title, body, deal=None, url="", campaign=None):
        """Базовый метод создания уведомления.

        Args:
            user (User):              получатель
            notification_type (str):  Notification.Type.*
            title (str):              заголовок (до 255 символов)
            body (str):               текст уведомления
            deal (Deal|None):         связанная сделка (если применимо)
            url (str):                куда вести по клику на уведомление (относительный путь)
            campaign (Campaign|None): связанная кампания — её страница тоже гасит уведомление
        """
        try:
            Notification.objects.create(
                user=user,
                type=notification_type,
                title=title,
                body=body,
                related_deal=deal,
                related_campaign=campaign,
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
            body=f"Блогер {blogger.public_name} откликнулся на кампанию «{campaign.name}».",
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
            campaign=campaign,
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
                f"Рекламодатель {advertiser.public_name} предлагает вам участие "
                f"в кампании «{campaign.name}». Проверьте входящие предложения."
            ),
            url=reverse("web:blogger_dashboard"),
        )

    @staticmethod
    def notify_offer_sent(offer):
        """Рекламодатель направил индивидуальную оферту (по отклику или прямую) → исполнителю."""
        from django.utils import timezone

        campaign = offer.campaign
        source = "по вашему отклику " if offer.response_id else ""
        NotificationService.notify(
            user=offer.blogger,
            notification_type=Notification.Type.DIRECT_OFFER_RECEIVED,
            title="Вам направлена оферта",
            body=(
                f"Рекламодатель {offer.advertiser.public_name} направил оферту {source}по кампании «{campaign.name}»: "
                f"{format_money(offer.reserved_amount)}, публикация {offer.publication_date:%d.%m.%Y}. "
                f"Примите до {timezone.localtime(offer.expires_at):%d.%m.%Y %H:%M} — иначе оферта истечёт."
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
                f"Блогер {blogger.public_name} принял ваше предложение по кампании "
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
                f"Блогер {blogger.public_name} отклонил ваше предложение "
                f"по кампании «{campaign.name}»."
            ),
            url=reverse("web:campaign_detail", kwargs={"pk": campaign.pk}),
            campaign=campaign,
        )

    # ── Сделки ────────────────────────────────────────────────────────────────

    @staticmethod
    def notify_deal_completed(blogger, deal, how="confirmed"):
        """Сделка завершена, деньги зачислены → блогеру. how: confirmed / accepted / timer — текст по пути."""
        reason = {
            "confirmed": "Рекламодатель подтвердил сделку",
            "accepted": "Публикация принята, срок сохранения истёк по сделке",
            "timer": "Сроки претензии и сохранения истекли без претензий по сделке",
        }.get(how, "Завершена сделка")
        if how == "timer" and deal.min_retention_days is None:
            reason = "72 часа без ответа рекламодателя — сделка подтверждена автоматически:"
        NotificationService.notify(
            user=blogger,
            notification_type=Notification.Type.PAYMENT_RECEIVED,
            title="Деньги зачислены на баланс",
            body=(
                f"{reason} #{deal.pk} "
                f"«{deal.campaign.name}». Средства переведены на ваш баланс."
            ),
            deal=deal,
            url=reverse("web:wallet"),
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
    def _staff():
        from apps.users.models import User

        return User.objects.filter(is_staff=True, is_active=True)

    @staticmethod
    def notify_claim_opened(claim):
        """Подана претензия → второй стороне (дать объяснения) и всем активным сотрудникам."""
        deal = claim.deal
        who = "Рекламодатель" if claim.author_id == deal.advertiser_id else "Блогер"
        NotificationService.notify(
            user=claim.respondent,
            notification_type=Notification.Type.DEAL_DISPUTED,
            title="Претензия по сделке",
            body=(f"{who} подал претензию по сделке #{deal.pk} «{deal.campaign.name}»: {claim.get_subject_display()}. "
                  f"Дайте объяснения и приложите материалы до "
                  f"{timezone.localtime(claim.answer_until):%d.%m.%Y %H:%M}. Деньги депонированы до решения."),
            deal=deal,
        )
        for staff in NotificationService._staff():
            NotificationService.notify(
                user=staff,
                notification_type=Notification.Type.DEAL_DISPUTED,
                title="Новая претензия",
                body=(f"Претензия по сделке #{deal.pk} «{deal.campaign.name}»: {claim.get_subject_display()}. "
                      f"Решение — до {timezone.localtime(claim.decide_until):%d.%m.%Y}."),
                url=reverse("web:admin_disputes"),
            )

    @staticmethod
    def notify_claim_materials(claim, actor):
        """Сторона добавила объяснения или материалы → сотрудникам."""
        deal = claim.deal
        for staff in NotificationService._staff():
            NotificationService.notify(
                user=staff,
                notification_type=Notification.Type.DEAL_DISPUTED,
                title="Новые материалы по претензии",
                body=f"По претензии к сделке #{deal.pk} «{deal.campaign.name}» добавлены материалы ({actor.public_name}).",
                url=reverse("web:admin_disputes"),
            )

    @staticmethod
    def notify_claim_extra_docs(claim):
        """Сотрудник запросил дополнительные документы → обеим сторонам."""
        deal = claim.deal
        for user in (deal.blogger, deal.advertiser):
            NotificationService.notify(
                user=user,
                notification_type=Notification.Type.DEAL_DISPUTED,
                title="Нужны дополнительные документы по претензии",
                body=(f"По сделке #{deal.pk} «{deal.campaign.name}» сотрудник просит дополнительные материалы до "
                      f"{timezone.localtime(claim.extra_docs_until):%d.%m.%Y}: {claim.decision_comment}"),
                deal=deal,
            )

    @staticmethod
    def notify_claim_decision_due(claim):
        """Подходит срок решения по претензии → сотрудникам."""
        deal = claim.deal
        for staff in NotificationService._staff():
            NotificationService.notify(
                user=staff,
                notification_type=Notification.Type.DEAL_DISPUTED,
                title="Срок решения по претензии",
                body=(f"Претензия по сделке #{deal.pk} «{deal.campaign.name}» ждёт решения до "
                      f"{timezone.localtime(claim.decide_until):%d.%m.%Y %H:%M}."),
                url=reverse("web:admin_disputes"),
            )

    @staticmethod
    def notify_dispute_resolved(deal):
        """Решение по претензии → обеим сторонам."""
        if deal.status == "cancelled":
            result = "средства возвращены рекламодателю"
        elif deal.paid_amount is not None and deal.paid_amount < deal.amount:
            result = (f"исполнителю перечислено {format_money(deal.paid_amount)}, "
                      f"остаток {format_money(deal.amount - deal.paid_amount)} возвращён рекламодателю")
        else:
            result = "оплата переведена исполнителю"
        for user in (deal.blogger, deal.advertiser):
            NotificationService.notify(
                user=user,
                notification_type=Notification.Type.DEAL_UPDATED,
                title="Решение по претензии",
                body=(f"По сделке #{deal.pk} «{deal.campaign.name}» принято решение: {result}. "
                      f"{deal.dispute_resolution}".strip()),
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
                f"«{deal.campaign.name}». Проверьте и согласуйте"
                + (f" или направьте замечания до {timezone.localtime(deal.review_due):%d.%m.%Y %H:%M}."
                   if deal.review_due else ".")
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
                + (f"Проверьте: претензию по размещению можно подать до "
                   f"{timezone.localtime(deal.claim_until):%d.%m.%Y %H:%M}, об удалении публикации — до "
                   f"{timezone.localtime(deal.retention_until):%d.%m.%Y %H:%M}. Без претензий оплата исполнителю — "
                   f"{timezone.localtime(deal.payout_due):%d.%m.%Y %H:%M}."
                   if deal.min_retention_days is not None else
                   "Проверьте и подтвердите — без ответа сделка завершится автоматически через 72 часа.")
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

    # ── Дата публикации ───────────────────────────────────────────────────────

    @staticmethod
    def notify_publication_accepted(deal):
        """Рекламодатель принял публикацию → исполнителю: когда придут деньги и до какого числа сохранять пост."""
        NotificationService.notify(
            user=deal.blogger,
            notification_type=Notification.Type.DEAL_UPDATED,
            title="Публикация принята",
            body=(
                f"Рекламодатель принял публикацию по сделке #{deal.pk} «{deal.campaign.name}». "
                f"Сохраняйте её до {timezone.localtime(deal.retention_until):%d.%m.%Y %H:%M} — оплата "
                f"{timezone.localtime(deal.payout_due):%d.%m.%Y %H:%M}."
            ),
            deal=deal,
        )

    @staticmethod
    def notify_review_overdue(deal):
        """Срок рассмотрения материала истёк без ответа → обеим сторонам."""
        due = f"{timezone.localtime(deal.review_due):%d.%m.%Y %H:%M}"
        NotificationService.notify(
            user=deal.advertiser,
            notification_type=Notification.Type.DEAL_UPDATED,
            title="Истёк срок рассмотрения материала",
            body=(
                f"По сделке #{deal.pk} «{deal.campaign.name}» материал нужно было рассмотреть до {due}. "
                f"Согласуйте его или направьте замечания на странице сделки."
            ),
            deal=deal,
        )
        NotificationService.notify(
            user=deal.blogger,
            notification_type=Notification.Type.DEAL_UPDATED,
            title="Рекламодатель не ответил на материал в срок",
            body=(
                f"По сделке #{deal.pk} «{deal.campaign.name}» срок рассмотрения материала истёк {due}. "
                f"Публиковать без согласования нельзя. Если дату публикации не выдержать — предложите перенос даты "
                f"на странице сделки."
            ),
            deal=deal,
        )

    @staticmethod
    def notify_publication_reminder(deal):
        """Завтра дата публикации → исполнителю."""
        NotificationService.notify(
            user=deal.blogger,
            notification_type=Notification.Type.DEAL_UPDATED,
            title="Завтра — дата публикации",
            body=(
                f"По сделке #{deal.pk} «{deal.campaign.name}» дата публикации — {deal.publication_date:%d.%m.%Y}. "
                f"Опубликуйте и добавьте ссылку на странице сделки или договоритесь о переносе даты."
            ),
            deal=deal,
        )

    @staticmethod
    def notify_publication_overdue(deal):
        """Дата публикации прошла, публикации нет → обеим сторонам."""
        day = f"{deal.publication_date:%d.%m.%Y}"
        NotificationService.notify(
            user=deal.blogger,
            notification_type=Notification.Type.DEAL_UPDATED,
            title="Дата публикации прошла",
            body=(
                f"По сделке #{deal.pk} «{deal.campaign.name}» дата публикации {day} прошла, публикации нет. "
                f"Опубликуйте и добавьте ссылку или предложите рекламодателю перенести дату."
            ),
            deal=deal,
        )
        NotificationService.notify(
            user=deal.advertiser,
            notification_type=Notification.Type.DEAL_UPDATED,
            title="Блогер не опубликовал к дате",
            body=(
                f"По сделке #{deal.pk} «{deal.campaign.name}» дата публикации {day} прошла, публикации нет. "
                f"Обсудите с блогером в чате сделки или согласуйте перенос даты."
            ),
            deal=deal,
        )

    @staticmethod
    def notify_publication_date_proposed(recipient, deal, change):
        """Вторая сторона предложила перенести дату публикации → получателю ответить."""
        NotificationService.notify(
            user=recipient,
            notification_type=Notification.Type.DEAL_UPDATED,
            title="Предложен перенос даты публикации",
            body=(
                f"{change.proposed_by.public_name} предлагает перенести дату публикации по сделке #{deal.pk} "
                f"«{deal.campaign.name}» с {change.old_date:%d.%m.%Y} на {change.new_date:%d.%m.%Y}. "
                f"Примите или отклоните на странице сделки."
            ),
            deal=deal,
        )

    @staticmethod
    def notify_publication_date_answered(proposer, deal, change):
        """Ответ на перенос даты публикации → автору предложения."""
        accepted = change.status == change.Status.ACCEPTED
        NotificationService.notify(
            user=proposer,
            notification_type=Notification.Type.DEAL_UPDATED,
            title="Перенос даты принят" if accepted else "Перенос даты отклонён",
            body=(
                f"Перенос даты публикации по сделке #{deal.pk} «{deal.campaign.name}» на "
                f"{change.new_date:%d.%m.%Y} " + ("принят — новая дата действует." if accepted
                                                  else f"отклонён. Дата остаётся {deal.publication_date:%d.%m.%Y}.")
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
    def notify_campaign_finished_early(campaign):
        """Рекламодатель завершил кампанию досрочно → ему же, подтверждение с последствиями."""
        NotificationService.notify(
            user=campaign.advertiser,
            notification_type=Notification.Type.CAMPAIGN_STATUS,
            title="Кампания завершена досрочно",
            body=(
                f"Кампания «{campaign.name}» завершена досрочно. Ожидавшие решения отклики и предложения "
                f"закрыты, начатые сделки продолжаются до конца и оплаты."
            ),
            url=reverse("web:campaign_detail", kwargs={"pk": campaign.pk}),
        )

    @staticmethod
    def notify_edit_proposal_closed(moderator, campaign):
        """Кампанию завершили, пока ждали ответа на правки модератора → автору правок."""
        NotificationService.notify(
            user=moderator,
            notification_type=Notification.Type.CAMPAIGN_STATUS,
            title="Предложение правок закрыто",
            body=f"Рекламодатель завершил кампанию «{campaign.name}» — ваше предложение правок закрыто без ответа.",
            url=reverse("web:admin_campaign_detail", kwargs={"pk": campaign.pk}),
        )

    @staticmethod
    def notify_deal_continues_after_campaign(deal):
        """Кампания завершена (по сроку или досрочно), а сделка идёт → блогеру."""
        NotificationService.notify(
            user=deal.blogger,
            notification_type=Notification.Type.CAMPAIGN_STATUS,
            title="Кампания завершена — ваша сделка продолжается",
            body=(
                f"Кампания «{deal.campaign.name}» завершена. Ваша сделка #{deal.pk} продолжается: "
                f"выполните её как обычно, оплата — по правилам сделки."
            ),
            deal=deal,
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

    # ── Срок ответа на отклик и предложение: 7 дней (BZ-3) ──────────────────────

    @staticmethod
    def _deadline(item):
        from django.utils import timezone

        return f"{timezone.localtime(item.expires_at):%d.%m.%Y %H:%M}"

    @staticmethod
    def notify_response_reminder(resp):
        """До конца срока ответа на отклик меньше суток → рекламодателю."""
        campaign = resp.campaign
        NotificationService.notify(
            user=campaign.advertiser,
            notification_type=Notification.Type.CAMPAIGN_RESPONSE,
            title="Ответьте на отклик",
            body=(
                f"Отклик блогера {resp.blogger.public_name} на кампанию «{campaign.name}» ждёт решения. "
                f"Ответить нужно до {NotificationService._deadline(resp)}, иначе отклик истечёт."
            ),
            url=reverse("web:campaign_detail", kwargs={"pk": campaign.pk}),
            campaign=campaign,
        )

    @staticmethod
    def notify_direct_offer_reminder(offer):
        """До конца срока ответа на предложение меньше суток → блогеру."""
        NotificationService.notify(
            user=offer.blogger,
            notification_type=Notification.Type.DIRECT_OFFER_RECEIVED,
            title="Ответьте на предложение",
            body=(
                f"Предложение по кампании «{offer.campaign.name}» ждёт вашего ответа. "
                f"Ответить нужно до {NotificationService._deadline(offer)}, иначе предложение истечёт."
            ),
            url=reverse("web:blogger_dashboard"),
        )

    @staticmethod
    def notify_response_timed_out(resp):
        """Отклик истёк без ответа за 7 дней → блогеру и рекламодателю."""
        campaign = resp.campaign
        NotificationService.notify(
            user=resp.blogger,
            notification_type=Notification.Type.CAMPAIGN_STATUS,
            title="Срок ответа на отклик истёк",
            body=(
                f"Рекламодатель не ответил на ваш отклик на кампанию «{campaign.name}» за 7 дней — отклик истёк. "
                f"Вы можете откликнуться снова."
            ),
            url=reverse("web:my_responses"),
        )
        NotificationService.notify(
            user=campaign.advertiser,
            notification_type=Notification.Type.CAMPAIGN_STATUS,
            title="Отклик истёк без ответа",
            body=f"Отклик блогера {resp.blogger.public_name} на кампанию «{campaign.name}» истёк: прошло 7 дней без ответа.",
            url=reverse("web:campaign_detail", kwargs={"pk": campaign.pk}),
            campaign=campaign,
        )

    @staticmethod
    def notify_direct_offer_timed_out(offer):
        """Предложение истекло без ответа за 7 дней → блогеру и рекламодателю."""
        campaign = offer.campaign
        NotificationService.notify(
            user=offer.blogger,
            notification_type=Notification.Type.CAMPAIGN_STATUS,
            title="Предложение истекло",
            body=f"Предложение по кампании «{campaign.name}» истекло: срок ответа прошёл.",
            url=reverse("web:blogger_dashboard"),
        )
        returned = (
            f" Зарезервированные {format_money(offer.reserved_amount)} возвращены на баланс."
            if offer.reserved_at else ""
        )
        NotificationService.notify(
            user=offer.advertiser,
            notification_type=Notification.Type.CAMPAIGN_STATUS,
            title="Предложение истекло без ответа",
            body=(
                f"Блогер {offer.blogger.public_name} не ответил на предложение по кампании «{campaign.name}» в срок."
                f"{returned} Вы можете отправить новое предложение."
            ),
            url=reverse("web:direct_offer_create", kwargs={"platform_pk": offer.platform_id}),
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

    # ── Реквизиты выплаты (T7/#39) ───────────────────────────────────────────

    @staticmethod
    def notify_payout_requisites_approved(blogger, application):
        """Реквизиты выплаты подтверждены → блогеру."""
        NotificationService.notify(
            user=blogger,
            notification_type=Notification.Type.PAYOUT_REQUISITES_APPROVED,
            title="Реквизиты выплаты подтверждены",
            body="Теперь вы можете подать заявку на вывод средств.",
            url=reverse("web:wallet"),
        )

    @staticmethod
    def notify_payout_requisites_rejected(blogger, application):
        """Реквизиты выплаты отклонены → блогеру."""
        reason = application.rejection_reason or "причина не указана"
        NotificationService.notify(
            user=blogger,
            notification_type=Notification.Type.PAYOUT_REQUISITES_REJECTED,
            title="Реквизиты выплаты отклонены",
            body=f"Причина: {reason}",
            url=reverse("web:payout_requisites"),
        )
