from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.billing.services import BillingService
from apps.campaigns.models import Campaign, DirectOffer
from apps.deals.models import Deal, DealStatusLog
from apps.notifications.service import NotificationService
from apps.platforms.models import Platform
from apps.users.models import User

from ..forms import CatalogFilterForm, DirectOfferForm
from .pages import _redirect_dashboard


@login_required
def blogger_catalog(request):
    """Каталог одобренных площадок блогеров (Модуль 10).

    Доступ: только рекламодатели (role=ADVERTISER) и is_staff.
    Блогеры и анонимные пользователи перенаправляются на свой дашборд / login.

    GET-параметры (CatalogFilterForm):
        social_type     — тип соцсети (instagram, telegram, youtube, …)
        category        — pk категории (Category)
        min_subscribers — минимальное число подписчиков
        max_subscribers — максимальное число подписчиков
        min_price       — цена за пост от (price_post__gte)
        max_price       — цена за пост до (price_post__lte)
        min_er          — ER% от
        max_er          — ER% до
        min_rating      — минимальный рейтинг блогера (BloggerProfile.rating)
        sort            — сортировка; по умолчанию: по рейтингу убывание

    Контекст шаблона:
        platforms — QuerySet[Platform] (только APPROVED)
        form      — CatalogFilterForm
        total     — int, количество найденных площадок
    """
    if not request.user.is_staff and request.user.role != User.Role.ADVERTISER:
        messages.error(request, "Каталог блогеров доступен только рекламодателям.")
        return _redirect_dashboard(request.user)

    form = CatalogFilterForm(request.GET or None)
    qs = (
        Platform.objects.filter(status=Platform.Status.APPROVED)
        .select_related("blogger__blogger_profile")
        .prefetch_related("categories")
    )

    if form.is_valid():
        cd = form.cleaned_data
        if cd.get("social_type"):
            qs = qs.filter(social_type=cd["social_type"])
        if cd.get("category"):
            qs = qs.filter(categories=cd["category"])
        if cd.get("min_subscribers"):
            qs = qs.filter(subscribers__gte=cd["min_subscribers"])
        if cd.get("max_subscribers"):
            qs = qs.filter(subscribers__lte=cd["max_subscribers"])
        if cd.get("min_price"):
            qs = qs.filter(price_post__gte=cd["min_price"])
        if cd.get("max_price"):
            qs = qs.filter(price_post__lte=cd["max_price"])
        if cd.get("min_er"):
            qs = qs.filter(engagement_rate__gte=cd["min_er"])
        if cd.get("max_er"):
            qs = qs.filter(engagement_rate__lte=cd["max_er"])
        if cd.get("min_rating"):
            qs = qs.filter(blogger__blogger_profile__rating__gte=cd["min_rating"])
        sort = cd.get("sort")
        if sort:
            qs = qs.order_by(sort)
        else:
            qs = qs.order_by("-blogger__blogger_profile__rating")
    else:
        qs = qs.order_by("-blogger__blogger_profile__rating")

    from django.core.paginator import Paginator
    total = qs.count()
    page_obj = Paginator(qs, 20).get_page(request.GET.get("page", 1))
    return render(request, "catalog/index.html", {
        "platforms": page_obj,
        "page_obj": page_obj,
        "form": form,
        "total": total,
    })


@login_required
def direct_offer_create(request, platform_pk):
    """Создание прямого предложения от рекламодателя блогеру (Модуль 10).

    Доступ: только role=ADVERTISER. Площадка должна быть APPROVED (иначе 404).

    GET  — отображает форму DirectOfferForm; если уже есть PENDING-оффер для
           этой площадки — показывает предупреждение вместо формы.
    POST — создаёт DirectOffer со статусом PENDING.
           Гарды:
             • нельзя создать второй оффер для той же (advertiser, campaign, platform),
               если предыдущий не REJECTED;
             • кампания должна принадлежать текущему рекламодателю и быть ACTIVE.
           При успехе: redirect → blogger_catalog с flash-сообщением.

    URL-параметры:
        platform_pk — pk площадки (Platform)

    Контекст шаблона:
        platform       — Platform
        blogger        — User (владелец площадки)
        form           — DirectOfferForm
        existing_offer — DirectOffer|None (PENDING оффер, если есть)
    """
    if request.user.role != User.Role.ADVERTISER:
        messages.error(request, "Только рекламодатели могут отправлять предложения.")
        return _redirect_dashboard(request.user)

    platform = get_object_or_404(Platform, pk=platform_pk, status=Platform.Status.APPROVED)
    blogger = platform.blogger

    # Check for existing pending offer
    existing = DirectOffer.objects.filter(
        advertiser=request.user,
        campaign__in=Campaign.objects.filter(advertiser=request.user),
        platform=platform,
        status=DirectOffer.Status.PENDING,
    ).first()

    form = DirectOfferForm(advertiser=request.user, data=request.POST or None)

    if request.method == "POST" and form.is_valid():
        campaign = form.cleaned_data["campaign"]

        # Guard: no duplicate offer for same advertiser+campaign+platform
        if DirectOffer.objects.filter(
            advertiser=request.user, campaign=campaign, platform=platform
        ).exclude(status=DirectOffer.Status.REJECTED).exists():
            messages.error(request, "Предложение для этой площадки в рамках данной кампании уже отправлено.")
            return redirect("web:blogger_public_profile", pk=blogger.pk)

        DirectOffer.objects.create(
            advertiser=request.user,
            blogger=blogger,
            campaign=campaign,
            platform=platform,
            content_type=form.cleaned_data["content_type"],
            proposed_price=form.cleaned_data.get("proposed_price"),
            message=form.cleaned_data.get("message", ""),
        )
        NotificationService.notify_direct_offer_received(blogger, campaign, request.user)
        messages.success(request, f"Предложение отправлено блогеру {blogger.email}!")
        return redirect("web:blogger_catalog")

    return render(request, "catalog/direct_offer.html", {
        "platform": platform,
        "blogger": blogger,
        "form": form,
        "existing_offer": existing,
    })


@login_required
@require_POST
def direct_offer_accept(request, pk):
    """Блогер принимает прямое предложение — создаётся Сделка (Модуль 10).

    Доступ: только владелец оффера (blogger=request.user), статус PENDING.
    Рекламодатель и чужие блогеры получают 404.

    Логика (всё внутри atomic + select_for_update):
        1. Проверка что кампания ACTIVE.
        2. Определение суммы сделки: proposed_price ?? campaign.fixed_price.
        3. Проверка лимита участников кампании (max_bloggers).
        4. Повторная блокировка оффера (select_for_update) — гард от двойного принятия.
        5. Deal.objects.create(status=WAITING_PAYMENT).
        6. BillingService.reserve_funds(deal) — резервирует средства у рекламодателя.
           При ValueError (нет средств) транзакция откатывается, deal=None.
        7. DealStatusLog.log → статус → IN_PROGRESS.
        8. DirectOffer.status → ACCEPTED, DirectOffer.deal → созданная сделка.

    При успехе: redirect → deal_detail.
    При ошибке резервирования: redirect → blogger_dashboard с сообщением об ошибке.

    URL-параметры:
        pk — pk DirectOffer
    """
    offer = get_object_or_404(
        DirectOffer, pk=pk, blogger=request.user, status=DirectOffer.Status.PENDING
    )

    campaign = offer.campaign
    if campaign.status != Campaign.Status.ACTIVE:
        messages.error(request, "Кампания больше не активна.")
        return redirect("web:blogger_dashboard")

    amount = offer.proposed_price or campaign.fixed_price
    if not amount:
        messages.error(request, "Не удалось определить сумму сделки.")
        return redirect("web:blogger_dashboard")

    from django.db import transaction as db_transaction
    deal = None
    try:
        with db_transaction.atomic():
            locked_campaign = Campaign.objects.select_for_update().get(pk=campaign.pk)
            if locked_campaign.max_bloggers > 0:
                active_count = Deal.objects.filter(
                    campaign=locked_campaign,
                    status__in=[
                        Deal.Status.IN_PROGRESS, Deal.Status.CHECKING,
                        Deal.Status.ON_APPROVAL, Deal.Status.WAITING_PUBLICATION,
                        Deal.Status.COMPLETED,
                    ],
                ).count()
                if active_count >= locked_campaign.max_bloggers:
                    messages.error(request, "Достигнут лимит участников кампании.")
                    return redirect("web:blogger_dashboard")

            locked_offer = DirectOffer.objects.select_for_update().get(pk=offer.pk)
            if locked_offer.status != DirectOffer.Status.PENDING:
                messages.error(request, "Предложение уже обработано.")
                return redirect("web:blogger_dashboard")

            deal = Deal.objects.create(
                campaign=locked_campaign,
                blogger=request.user,
                platform=offer.platform,
                advertiser=offer.advertiser,
                amount=amount,
                status=Deal.Status.WAITING_PAYMENT,
            )
            BillingService.reserve_funds(deal)
            DealStatusLog.log(deal, Deal.Status.IN_PROGRESS, changed_by=offer.advertiser, comment="Accepted direct offer.")
            deal.status = Deal.Status.IN_PROGRESS
            deal.save(update_fields=["status"])

            locked_offer.status = DirectOffer.Status.ACCEPTED
            locked_offer.deal = deal
            locked_offer.save(update_fields=["status", "deal", "updated_at"])

        NotificationService.notify_direct_offer_accepted(
            offer.advertiser, locked_campaign, request.user, deal
        )
        messages.success(request, f"Предложение принято. Сделка #{deal.pk} создана!")
    except ValueError as e:
        deal = None
        messages.error(request, f"Недостаточно средств у рекламодателя: {e}")

    return redirect("web:deal_detail", pk=deal.pk) if deal else redirect("web:blogger_dashboard")


@login_required
@require_POST
def direct_offer_reject(request, pk):
    """Блогер отклоняет прямое предложение (Модуль 10).

    Доступ: только владелец оффера (blogger=request.user), статус PENDING.
    Рекламодатель и чужие блогеры получают 404.

    Устанавливает DirectOffer.status = REJECTED.
    Финансовых операций не производит (деньги не резервировались).

    При успехе: redirect → blogger_dashboard с flash-сообщением.

    URL-параметры:
        pk — pk DirectOffer
    """
    offer = get_object_or_404(
        DirectOffer, pk=pk, blogger=request.user, status=DirectOffer.Status.PENDING
    )
    offer.status = DirectOffer.Status.REJECTED
    offer.save(update_fields=["status", "updated_at"])
    NotificationService.notify_direct_offer_rejected(offer.advertiser, offer.campaign, request.user)
    messages.success(request, "Предложение отклонено.")
    return redirect("web:blogger_dashboard")
