from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.campaigns.models import Campaign, DirectOffer
from apps.campaigns.services import AcceptError, accept_direct_offer, reject_offer, send_offer
from apps.campaigns.validation import publication_calendar
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
        ).exclude(status__in=[DirectOffer.Status.REJECTED, DirectOffer.Status.EXPIRED]).exists():
            messages.error(request, "Предложение для этой площадки в рамках данной кампании уже отправлено.")
            return redirect("web:blogger_public_profile", pk=blogger.pk)

        # Индивидуальная оферта: резерв суммы, дата публикации, срок акцепта (apps/campaigns/services.send_offer).
        try:
            offer = send_offer(
                campaign_pk=campaign.pk, advertiser=request.user, blogger=blogger, platform=platform,
                publication_date=form.cleaned_data["publication_date"],
                content_type=form.cleaned_data["content_type"],
                price=form.cleaned_data.get("proposed_price"),
                message=form.cleaned_data.get("message", ""),
            )
        except AcceptError as e:
            form.add_error(None, str(e))
        else:
            messages.success(
                request,
                f"Оферта направлена блогеру {blogger.public_name}: публикация {offer.publication_date:%d.%m.%Y}, "
                f"сумма в резерве. Сделка будет заключена, когда блогер примет оферту.",
            )
            return redirect("web:blogger_catalog")

    return render(request, "catalog/direct_offer.html", {
        "platform": platform,
        "blogger": blogger,
        "form": form,
        "existing_offer": existing,
        "calendars": [
            (c, publication_calendar(c)) for c in form.fields["campaign"].queryset
        ],
    })


@login_required
@require_POST
def direct_offer_accept(request, pk):
    """Блогер принимает прямое предложение — логика в apps/campaigns/services.accept_direct_offer."""
    offer = get_object_or_404(DirectOffer, pk=pk, blogger=request.user)
    try:
        deal = accept_direct_offer(offer.pk, request.user)
    except AcceptError as e:
        messages.error(request, str(e))
        return redirect("web:blogger_dashboard")
    messages.success(request, f"Предложение принято. Сделка #{deal.pk} создана!")
    return redirect("web:deal_detail", pk=deal.pk)



@login_required
@require_POST
def direct_offer_reject(request, pk):
    """Блогер отклоняет прямое предложение (Модуль 10).

    Доступ: только владелец оффера (blogger=request.user), статус PENDING.
    Рекламодатель и чужие блогеры получают 404.

    Логика — apps/campaigns/services.reject_offer: REJECTED, резерв возвращается рекламодателю.

    При успехе: redirect → blogger_dashboard с flash-сообщением.

    URL-параметры:
        pk — pk DirectOffer
    """
    offer = get_object_or_404(
        DirectOffer, pk=pk, blogger=request.user, status=DirectOffer.Status.PENDING
    )
    try:
        reject_offer(offer.pk, request.user)
    except AcceptError as e:
        messages.error(request, str(e))
        return redirect("web:blogger_dashboard")
    messages.success(request, "Оферта отклонена.")
    return redirect("web:blogger_dashboard")
