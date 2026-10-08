"""Завершение кампании по сроку — одно место для таймера и для проверок «срок истёк».

Кампания действует по дату окончания включительно. На следующий день ACTIVE и PAUSED кампании
завершаются: возобновлять кампанию после её срока бессмысленно. Ожидающие отклики и прямые
предложения по ней закрываются статусом EXPIRED (принять их уже нельзя), идущие сделки не трогаются —
у них свои таймеры.
"""
from django.db import transaction
from django.utils import timezone

from apps.notifications.service import NotificationService

from .models import RESPONSE_REMINDER_BEFORE, Campaign, DirectOffer, Response

COMPLETABLE_STATUSES = (Campaign.Status.ACTIVE, Campaign.Status.PAUSED)


def is_expired(campaign, today=None):
    """Срок кампании истёк: дата окончания задана и уже прошла."""
    today = today or timezone.localdate()
    return bool(campaign.end_date and campaign.end_date < today)


def expired_error(campaign, today=None):
    """Сообщение для действий, которые нельзя выполнять над кампанией с истёкшим сроком, — или None."""
    if not is_expired(campaign, today):
        return None
    return (
        f"Срок кампании истёк ({campaign.end_date:%d.%m.%Y}). "
        f"Чтобы продолжить, рекламодатель должен продлить дату окончания."
    )


def complete_campaign(campaign_pk, today=None, actor=None):
    """Завершить кампанию — по сроку (таймер) или досрочно владельцем (``actor``). Последствия одни.

    По сроку: только ACTIVE/PAUSED с прошедшей датой окончания, иначе False. Досрочно: владелец, статус —
    ACTIVE, PAUSED или MODERATION после одобрения (Р7), иначе AcceptError с текстом; ставится отметка
    ``completed_early_at``, ожидающее предложение правок закрывается. Возвращает True, если кампания
    завершена этим вызовом.
    """
    from .models import CampaignEditProposal

    today = today or timezone.localdate()
    early = actor is not None
    with transaction.atomic():
        campaign = Campaign.objects.select_for_update().filter(pk=campaign_pk).first()
        if early:
            if campaign is None or campaign.advertiser_id != actor.pk:
                raise AcceptError("Кампания не найдена.")
            if not campaign.can_finish_early:
                raise AcceptError(
                    "Завершить можно активную кампанию, кампанию на паузе или на повторной модерации "
                    "после одобрения."
                )
        elif campaign is None or campaign.status not in COMPLETABLE_STATUSES or not is_expired(campaign, today):
            return False
        campaign.status = Campaign.Status.COMPLETED
        fields = ["status", "updated_at"]
        if early:
            campaign.completed_early_at = timezone.now()
            fields.append("completed_early_at")
        campaign.save(update_fields=fields)

        responses = list(
            Response.objects.select_for_update()
            .filter(campaign=campaign, status=Response.Status.PENDING)
            .select_related("blogger")
        )
        offers = list(
            DirectOffer.objects.select_for_update()
            .filter(campaign=campaign, status=DirectOffer.Status.PENDING)
            .select_related("blogger")
        )
        Response.objects.filter(pk__in=[r.pk for r in responses]).update(status=Response.Status.EXPIRED)
        DirectOffer.objects.filter(pk__in=[o.pk for o in offers]).update(status=DirectOffer.Status.EXPIRED)

        proposal = (
            CampaignEditProposal.objects.select_for_update()
            .filter(campaign=campaign, status=CampaignEditProposal.Status.PENDING).first()
        )
        if proposal:
            proposal.status = CampaignEditProposal.Status.CLOSED
            proposal.responded_at = timezone.now()
            proposal.save(update_fields=["status", "responded_at"])

    if early:
        NotificationService.notify_campaign_finished_early(campaign)
    else:
        NotificationService.notify_campaign_completed(campaign)
    for resp in responses:
        NotificationService.notify_response_expired(resp.blogger, campaign)
    for offer in offers:
        NotificationService.notify_direct_offer_expired(offer.blogger, campaign)
    if proposal and proposal.author:
        NotificationService.notify_edit_proposal_closed(proposal.author, campaign)
    _notify_bloggers_with_running_deals(campaign)
    return True


def _notify_bloggers_with_running_deals(campaign):
    """Кампания завершена, а сделки идут — блогеру: «ваша сделка продолжается»."""
    from apps.deals.models import Deal

    finished = (Deal.Status.COMPLETED, Deal.Status.CANCELLED)
    for deal in Deal.objects.filter(campaign=campaign).exclude(status__in=finished).select_related("blogger", "campaign"):
        NotificationService.notify_deal_continues_after_campaign(deal)


def complete_expired_campaigns(today=None):
    """Завершить все кампании с истёкшим сроком. Возвращает число завершённых."""
    today = today or timezone.localdate()
    pks = Campaign.objects.filter(
        status__in=COMPLETABLE_STATUSES, end_date__lt=today,
    ).values_list("pk", flat=True)
    return sum(1 for pk in list(pks) if complete_campaign(pk, today))


# ── Срок ответа на отклик и прямое предложение: 7 дней, напоминание за сутки (BZ-3) ──

def expire_overdue_responses_and_offers(now=None):
    """Ожидающие отклики и предложения с прошедшим сроком → EXPIRED, обе стороны уведомлены.

    Срок идёт независимо от статуса кампании (Р6). Возвращает (откликов, предложений).
    """
    now = now or timezone.now()
    with transaction.atomic():
        responses = list(
            Response.objects.select_for_update()
            .filter(status=Response.Status.PENDING, expires_at__lte=now)
            .select_related("blogger", "campaign__advertiser")
        )
        offers = list(
            DirectOffer.objects.select_for_update()
            .filter(status=DirectOffer.Status.PENDING, expires_at__lte=now)
            .select_related("blogger", "advertiser", "campaign")
        )
        Response.objects.filter(pk__in=[r.pk for r in responses]).update(status=Response.Status.EXPIRED)
        DirectOffer.objects.filter(pk__in=[o.pk for o in offers]).update(status=DirectOffer.Status.EXPIRED)

    for resp in responses:
        NotificationService.notify_response_timed_out(resp)
    for offer in offers:
        NotificationService.notify_direct_offer_timed_out(offer)
    return len(responses), len(offers)


def send_response_offer_reminders(now=None):
    """За сутки до срока — одно напоминание тому, кто должен ответить. Возвращает число напоминаний."""
    now = now or timezone.now()
    window = dict(status="pending", reminder_sent_at__isnull=True, expires_at__gt=now,
                  expires_at__lte=now + RESPONSE_REMINDER_BEFORE)
    sent = 0
    for model, notify in ((Response, NotificationService.notify_response_reminder),
                          (DirectOffer, NotificationService.notify_direct_offer_reminder)):
        with transaction.atomic():
            items = list(model.objects.select_for_update().filter(**window).select_related("campaign"))
            model.objects.filter(pk__in=[i.pk for i in items]).update(reminder_sent_at=now)
        for item in items:
            notify(item)
        sent += len(items)
    return sent


# ── Принятие отклика и прямого предложения: одна логика для сайта и API ──────

class AcceptError(Exception):
    """Отклик или предложение принять нельзя — текст понятен пользователю."""


def accept_response(response_pk, actor):
    """Рекламодатель принимает отклик: сделка в работе, деньги в резерве.

    Под блокировкой кампании и отклика: статус отклика, активность кампании, лимит блогеров, бюджет, срок.
    Нехватка денег на балансе — AcceptError с текстом; транзакция откатывается целиком.
    """
    from apps.deals.services import create_deal

    from .validation import deal_acceptance_error

    with transaction.atomic():
        resp = Response.objects.select_for_update().select_related("campaign").filter(pk=response_pk).first()
        if resp is None or resp.campaign.advertiser_id != getattr(actor, "pk", None):
            raise AcceptError("Отклик не найден.")
        campaign = Campaign.objects.select_for_update().get(pk=resp.campaign_id)
        if resp.status != Response.Status.PENDING:
            raise AcceptError("Можно принять только ожидающий отклик.")
        if resp.is_overdue:
            raise AcceptError(f"Срок ответа на отклик истёк ({timezone.localtime(resp.expires_at):%d.%m.%Y %H:%M}).")
        if campaign.status != Campaign.Status.ACTIVE:
            raise AcceptError("Нельзя принимать отклики — кампания не активна.")
        amount = resp.proposed_price or campaign.fixed_price
        if not amount:
            raise AcceptError("Не удалось определить сумму сделки: нет ни цены блогера, ни цены кампании.")
        error = deal_acceptance_error(campaign, amount)
        if error:
            raise AcceptError(error)
        resp.status = Response.Status.ACCEPTED
        resp.save(update_fields=["status", "updated_at"])
        try:
            deal = create_deal(
                campaign=campaign, blogger=resp.blogger, platform=resp.platform, advertiser=actor,
                amount=amount, actor=actor, response=resp, comment="Отклик принят, деньги зарезервированы.",
            )
        except ValueError as e:
            transaction.set_rollback(True)
            raise AcceptError(f"Недостаточно средств на балансе: {e}") from e
    NotificationService.notify_response_accepted(resp.blogger, campaign, deal)
    return deal


# ── Увеличение бюджета без паузы и модерации (решение бизнеса 07.10.2026) ────

def increase_budget(campaign_pk, new_budget, actor):
    """Увеличить бюджет кампании, не трогая статус и модерацию.

    Новый снимок (``approved_snapshot``) обновляется тем же бюджетом, чтобы следующая
    ре-модерация не показала увеличение как непроверенную правку (Р8).
    """
    from apps.web.campaign_proposals import mark_approved

    with transaction.atomic():
        campaign = Campaign.objects.select_for_update().filter(pk=campaign_pk).first()
        if campaign is None or campaign.advertiser_id != getattr(actor, "pk", None):
            raise AcceptError("Кампания не найдена.")
        if campaign.status not in COMPLETABLE_STATUSES:
            raise AcceptError("Увеличить бюджет можно только у активной кампании или кампании на паузе.")
        error = expired_error(campaign)
        if error:
            raise AcceptError(error)
        if new_budget is None or new_budget <= campaign.budget:
            raise AcceptError("Новый бюджет должен быть больше текущего.")
        campaign.budget = new_budget
        mark_approved(campaign)
        campaign.save(update_fields=["budget", "approved_snapshot", "updated_at"])
    return campaign


def accept_direct_offer(offer_pk, actor):
    """Блогер принимает прямое предложение: сделка в работе, деньги рекламодателя в резерве."""
    from apps.deals.services import create_deal

    from .validation import deal_acceptance_error

    with transaction.atomic():
        offer = DirectOffer.objects.select_for_update().select_related("campaign").filter(pk=offer_pk).first()
        if offer is None or offer.blogger_id != getattr(actor, "pk", None):
            raise AcceptError("Предложение не найдено.")
        campaign = Campaign.objects.select_for_update().get(pk=offer.campaign_id)
        if offer.status != DirectOffer.Status.PENDING:
            raise AcceptError("Предложение уже обработано.")
        if offer.is_overdue:
            raise AcceptError(f"Срок ответа на предложение истёк ({timezone.localtime(offer.expires_at):%d.%m.%Y %H:%M}).")
        if campaign.status != Campaign.Status.ACTIVE:
            raise AcceptError("Кампания больше не активна.")
        amount = offer.proposed_price or campaign.fixed_price
        if not amount:
            raise AcceptError("Не удалось определить сумму сделки.")
        error = deal_acceptance_error(campaign, amount)
        if error:
            raise AcceptError(error)
        try:
            deal = create_deal(
                campaign=campaign, blogger=actor, platform=offer.platform, advertiser=offer.advertiser,
                amount=amount, actor=actor, comment="Блогер принял прямое предложение, деньги зарезервированы.",
            )
        except ValueError as e:
            transaction.set_rollback(True)
            raise AcceptError(f"Недостаточно средств у рекламодателя: {e}") from e
        offer.status = DirectOffer.Status.ACCEPTED
        offer.deal = deal
        offer.save(update_fields=["status", "deal", "updated_at"])
    NotificationService.notify_direct_offer_accepted(offer.advertiser, campaign, actor, deal)
    return deal
