"""Завершение кампании по сроку — одно место для таймера и для проверок «срок истёк».

Кампания действует по дату окончания включительно. На следующий день ACTIVE и PAUSED кампании
завершаются: возобновлять кампанию после её срока бессмысленно. Ожидающие отклики и прямые
предложения по ней закрываются статусом EXPIRED (принять их уже нельзя), идущие сделки не трогаются —
у них свои таймеры.
"""
from django.db import transaction
from django.utils import timezone

from apps.notifications.service import NotificationService

from .models import Campaign, DirectOffer, Response

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


def complete_campaign(campaign_pk, today=None):
    """Завершить одну кампанию по сроку. Возвращает True, если кампания завершена этим вызовом."""
    today = today or timezone.localdate()
    with transaction.atomic():
        campaign = Campaign.objects.select_for_update().filter(pk=campaign_pk).first()
        if campaign is None or campaign.status not in COMPLETABLE_STATUSES or not is_expired(campaign, today):
            return False
        campaign.status = Campaign.Status.COMPLETED
        campaign.save(update_fields=["status", "updated_at"])

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

    NotificationService.notify_campaign_completed(campaign)
    for resp in responses:
        NotificationService.notify_response_expired(resp.blogger, campaign)
    for offer in offers:
        NotificationService.notify_direct_offer_expired(offer.blogger, campaign)
    return True


def complete_expired_campaigns(today=None):
    """Завершить все кампании с истёкшим сроком. Возвращает число завершённых."""
    today = today or timezone.localdate()
    pks = Campaign.objects.filter(
        status__in=COMPLETABLE_STATUSES, end_date__lt=today,
    ).values_list("pk", flat=True)
    return sum(1 for pk in list(pks) if complete_campaign(pk, today))


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
