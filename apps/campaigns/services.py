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
