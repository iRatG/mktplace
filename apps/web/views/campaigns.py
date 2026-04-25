from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.billing.services import BillingService
from apps.campaigns.models import Campaign
from apps.campaigns.models import Response as CampaignResponse
from apps.deals.models import Deal, DealStatusLog
from apps.notifications.service import NotificationService
from apps.platforms.models import Platform
from apps.users.models import User

from ..forms import CampaignForm
from .pages import _redirect_dashboard


@login_required
def campaign_list(request):
    from django.core.paginator import Paginator
    user = request.user
    if user.is_staff:
        qs = Campaign.objects.all().select_related("category").order_by("-created_at")
    elif user.role == User.Role.ADVERTISER:
        qs = Campaign.objects.filter(advertiser=user).select_related("category").order_by("-created_at")
    else:
        qs = Campaign.objects.filter(status=Campaign.Status.ACTIVE).select_related("category").order_by("-created_at")
    page_obj = Paginator(qs, 20).get_page(request.GET.get("page", 1))
    return render(request, "campaigns/list.html", {"campaigns": page_obj, "page_obj": page_obj})


@login_required
def campaign_detail(request, pk):
    user = request.user
    if user.is_staff:
        campaign = get_object_or_404(Campaign, pk=pk)
        responses = campaign.responses.select_related("blogger", "platform").order_by("-created_at")
        context = {
            "campaign": campaign,
            "is_owner": True,
            "responses": responses,
        }
    elif user.role == User.Role.ADVERTISER:
        campaign = get_object_or_404(Campaign, pk=pk, advertiser=user)
        responses = campaign.responses.select_related("blogger", "platform").order_by("-created_at")
        context = {
            "campaign": campaign,
            "is_owner": True,
            "responses": responses,
        }
    else:
        campaign = get_object_or_404(Campaign, pk=pk, status=Campaign.Status.ACTIVE)
        already_responded = CampaignResponse.objects.filter(
            campaign=campaign, blogger=user
        ).exclude(status=CampaignResponse.Status.WITHDRAWN).exists()
        my_platforms = Platform.objects.filter(blogger=user, status=Platform.Status.APPROVED)
        context = {
            "campaign": campaign,
            "is_owner": False,
            "already_responded": already_responded,
            "my_platforms": my_platforms,
        }
    return render(request, "campaigns/detail.html", context)


@login_required
def campaign_create(request):
    if request.user.role != User.Role.ADVERTISER:
        messages.error(request, "Только рекламодатели могут создавать кампании.")
        return _redirect_dashboard(request.user)

    form = CampaignForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        campaign = form.save(commit=False)
        campaign.advertiser = request.user
        campaign.content_types = form.cleaned_data.get("content_types", [])
        campaign.allowed_socials = form.cleaned_data.get("allowed_socials", [])
        campaign.save()
        messages.success(request, f"Кампания «{campaign.name}» создана.")
        return redirect("web:campaign_detail", pk=campaign.pk)

    return render(request, "campaigns/create.html", {"form": form})


@login_required
def campaign_edit(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk, advertiser=request.user)
    if campaign.status not in (Campaign.Status.DRAFT, Campaign.Status.REJECTED):
        messages.error(request, "Редактировать можно только черновики и отклонённые кампании.")
        return redirect("web:campaign_detail", pk=pk)

    form = CampaignForm(request.POST or None, instance=campaign)
    if request.method == "POST" and form.is_valid():
        campaign = form.save(commit=False)
        campaign.content_types = form.cleaned_data.get("content_types", [])
        campaign.allowed_socials = form.cleaned_data.get("allowed_socials", [])
        campaign.save()
        messages.success(request, "Кампания обновлена.")
        return redirect("web:campaign_detail", pk=campaign.pk)

    return render(request, "campaigns/create.html", {"form": form, "campaign": campaign})


@login_required
@require_POST
def campaign_submit(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk, advertiser=request.user)
    if campaign.status != Campaign.Status.DRAFT:
        messages.error(request, "Только черновики можно отправить на модерацию.")
    else:
        campaign.status = Campaign.Status.MODERATION
        campaign.save(update_fields=["status"])
        messages.success(request, "Кампания отправлена на модерацию.")
    return redirect("web:campaign_detail", pk=pk)


@login_required
@require_POST
def campaign_pause(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk, advertiser=request.user)
    if campaign.status != Campaign.Status.ACTIVE:
        messages.error(request, "Можно приостановить только активную кампанию.")
    else:
        campaign.status = Campaign.Status.PAUSED
        campaign.save(update_fields=["status"])
        messages.success(request, "Кампания приостановлена.")
    return redirect("web:campaign_detail", pk=pk)


@login_required
@require_POST
def campaign_resume(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk, advertiser=request.user)
    if campaign.status != Campaign.Status.PAUSED:
        messages.error(request, "Можно возобновить только приостановленную кампанию.")
    else:
        campaign.status = Campaign.Status.ACTIVE
        campaign.save(update_fields=["status"])
        messages.success(request, "Кампания возобновлена.")
    return redirect("web:campaign_detail", pk=pk)


@login_required
@require_POST
def campaign_respond(request, pk):
    if request.user.role != User.Role.BLOGGER:
        messages.error(request, "Только блогеры могут откликаться.")
        return redirect("web:campaign_detail", pk=pk)

    campaign = get_object_or_404(Campaign, pk=pk, status=Campaign.Status.ACTIVE)

    already = CampaignResponse.objects.filter(
        campaign=campaign, blogger=request.user
    ).exclude(status=CampaignResponse.Status.WITHDRAWN).exists()
    if already:
        messages.error(request, "Вы уже откликнулись на эту кампанию.")
        return redirect("web:campaign_detail", pk=pk)

    platform_id = request.POST.get("platform")
    content_type = request.POST.get("content_type", "")
    proposed_price = request.POST.get("proposed_price") or None
    message = request.POST.get("message", "")

    platform = get_object_or_404(Platform, pk=platform_id, blogger=request.user, status=Platform.Status.APPROVED)

    CampaignResponse.objects.create(
        blogger=request.user,
        campaign=campaign,
        platform=platform,
        content_type=content_type,
        proposed_price=proposed_price,
        message=message,
    )
    NotificationService.notify_new_response(campaign.advertiser, campaign, request.user)
    messages.success(request, "Отклик успешно отправлен!")
    return redirect("web:campaign_detail", pk=pk)


@login_required
@require_POST
def response_accept(request, pk):
    resp = get_object_or_404(CampaignResponse, pk=pk, campaign__advertiser=request.user)
    if resp.status != CampaignResponse.Status.PENDING:
        messages.error(request, "Можно принять только ожидающий отклик.")
        return redirect("web:campaign_detail", pk=resp.campaign_id)

    from django.db import transaction as db_transaction

    campaign = resp.campaign

    # Проверяем статус кампании
    if campaign.status != Campaign.Status.ACTIVE:
        messages.error(request, "Нельзя принимать отклики — кампания не активна.")
        return redirect("web:campaign_detail", pk=campaign.pk)

    # Проверяем лимит блогеров
    if campaign.max_bloggers > 0:
        active_deals_count = Deal.objects.filter(
            campaign=campaign,
            status__in=[
                Deal.Status.IN_PROGRESS,
                Deal.Status.CHECKING,
                Deal.Status.ON_APPROVAL,
                Deal.Status.WAITING_PUBLICATION,
                Deal.Status.COMPLETED,
            ],
        ).count()
        if active_deals_count >= campaign.max_bloggers:
            messages.error(request, f"Достигнут лимит блогеров для кампании ({campaign.max_bloggers}).")
            return redirect("web:campaign_detail", pk=campaign.pk)

    amount = resp.proposed_price or campaign.fixed_price
    if not amount:
        messages.error(request, "Не удалось определить сумму сделки.")
        return redirect("web:campaign_detail", pk=campaign.pk)

    try:
        with db_transaction.atomic():
            # Re-check limit inside atomic with lock to prevent race condition
            locked_campaign = Campaign.objects.select_for_update().get(pk=campaign.pk)
            if locked_campaign.max_bloggers > 0:
                active_count = Deal.objects.filter(
                    campaign=locked_campaign,
                    status__in=[
                        Deal.Status.IN_PROGRESS,
                        Deal.Status.CHECKING,
                        Deal.Status.ON_APPROVAL,
                        Deal.Status.WAITING_PUBLICATION,
                        Deal.Status.COMPLETED,
                    ],
                ).count()
                if active_count >= locked_campaign.max_bloggers:
                    messages.error(request, f"Достигнут лимит блогеров для кампании ({locked_campaign.max_bloggers}).")
                    return redirect("web:campaign_detail", pk=campaign.pk)

            resp.status = CampaignResponse.Status.ACCEPTED
            resp.save(update_fields=["status"])

            deal = Deal.objects.create(
                campaign=campaign,
                blogger=resp.blogger,
                platform=resp.platform,
                advertiser=request.user,
                response=resp,
                amount=amount,
                status=Deal.Status.WAITING_PAYMENT,
            )
            BillingService.reserve_funds(deal)
            DealStatusLog.log(deal, Deal.Status.IN_PROGRESS, changed_by=request.user, comment="Accepted via web.")
            deal.status = Deal.Status.IN_PROGRESS
            deal.save(update_fields=["status"])
        NotificationService.notify_response_accepted(resp.blogger, campaign, deal)
        messages.success(request, f"Отклик принят. Сделка #{deal.pk} создана.")
    except ValueError as e:
        deal = None
        messages.error(request, f"Недостаточно средств: {e}")

    return redirect("web:campaign_detail", pk=campaign.pk)


@login_required
@require_POST
def response_reject(request, pk):
    resp = get_object_or_404(CampaignResponse, pk=pk, campaign__advertiser=request.user)
    if resp.status == CampaignResponse.Status.PENDING:
        resp.status = CampaignResponse.Status.REJECTED
        resp.save(update_fields=["status"])
        NotificationService.notify_response_rejected(resp.blogger, resp.campaign)
        messages.success(request, "Отклик отклонён.")
    return redirect("web:campaign_detail", pk=resp.campaign_id)
