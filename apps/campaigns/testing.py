"""Помощники для тестов: сделка заключается акцептом оферты (#33).

«Принять отклик» направляет оферту, сделка появляется, когда исполнитель её принял. Тестам, которым нужна сделка
по отклику, — `deal_from_response` (сервисы) или `accept_web` + `blogger_accepts` (сайт).
"""
from django.urls import reverse
from django.utils import timezone


def publication_day(campaign):
    """Допустимая дата публикации для кампании: сегодня, но не раньше её начала."""
    today = timezone.localdate()
    if campaign.start_date and campaign.start_date > today:
        return campaign.start_date
    return today


def accept_web(client, advertiser, response, publication_date=None):
    """Рекламодатель на сайте принимает отклик — направляет оферту с датой публикации."""
    client.force_login(advertiser)
    day = publication_date or publication_day(response.campaign)
    return client.post(
        reverse("web:response_accept", kwargs={"pk": response.pk}), {"publication_date": day.isoformat()},
    )


def blogger_accepts(response):
    """Исполнитель принимает оферту по своему отклику — возвращает сделку."""
    from .models import DirectOffer
    from .services import accept_direct_offer

    offer = DirectOffer.objects.get(response=response)
    return accept_direct_offer(offer.pk, response.blogger)


def deal_from_response(response, advertiser, publication_date=None):
    """Принять отклик и акцепт оферты исполнителем — сделка «В работе»."""
    from .services import accept_direct_offer, accept_response

    offer = accept_response(response.pk, advertiser, publication_date or publication_day(response.campaign))
    return accept_direct_offer(offer.pk, response.blogger)
