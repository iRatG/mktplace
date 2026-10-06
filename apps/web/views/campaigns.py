from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.billing.services import BillingService
from apps.campaigns.models import Campaign, CampaignEditProposal
from apps.campaigns.models import Response as CampaignResponse
from apps.campaigns.validation import EDITABLE_STATUSES, deals_in_cap
from apps.deals.models import Deal, DealStatusLog
from apps.notifications.service import NotificationService
from apps.platforms.models import Platform
from apps.users.models import User

from ..campaign_proposals import describe_changes, proposal_form, save_campaign_form
from ..forms import CampaignForm, _strip_spaces
from .pages import _redirect_dashboard


def _responses_with_blogger_data(campaign):
    """Отклики с данными блогера и площадки — без N+1 (ник, рейтинг, метрики, категории)."""
    return (
        campaign.responses
        .select_related("blogger__blogger_profile", "platform")
        .prefetch_related("platform__categories")
        .order_by("-created_at")
    )


def _pending_proposal_context(campaign):
    proposal = campaign.edit_proposals.filter(status=CampaignEditProposal.Status.PENDING).first()
    return {"proposal": proposal, "proposal_rows": describe_changes(proposal) if proposal else []}


def _parse_price(raw):
    """«150 000» → Decimal("150000"); пусто → None; некорректное или отрицательное → ValueError."""
    from decimal import Decimal, InvalidOperation

    cleaned = _strip_spaces(raw or "").replace(",", ".")
    if not cleaned:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(raw)
    if value < 0 or not value.is_finite():
        raise ValueError(raw)
    return value


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
        responses = _responses_with_blogger_data(campaign)
        context = {
            "campaign": campaign,
            "is_owner": True,
            "responses": responses,
            **_pending_proposal_context(campaign),
        }
    elif user.role == User.Role.ADVERTISER:
        campaign = get_object_or_404(Campaign, pk=pk, advertiser=user)
        responses = _responses_with_blogger_data(campaign)
        context = {
            "campaign": campaign,
            "is_owner": True,
            "responses": responses,
            **_pending_proposal_context(campaign),
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
    if campaign.status not in EDITABLE_STATUSES:
        messages.error(request, "Редактировать можно черновики, отклонённые и приостановленные кампании.")
        return redirect("web:campaign_detail", pk=pk)

    was_paused = campaign.status == Campaign.Status.PAUSED
    form = CampaignForm(request.POST or None, instance=campaign)
    if request.method == "POST" and form.is_valid():
        campaign = form.save(commit=False)
        campaign.content_types = form.cleaned_data.get("content_types", [])
        campaign.allowed_socials = form.cleaned_data.get("allowed_socials", [])
        if was_paused:
            # Решение бизнеса 04.10.2026: идущую кампанию меняют только через повторную модерацию.
            campaign.status = Campaign.Status.MODERATION
        campaign.save()
        if was_paused:
            NotificationService.notify_campaign_moderation_requested(
                campaign, NotificationService.MODERATION_AFTER_PAUSE_EDIT
            )
            messages.success(request, "Изменения сохранены и отправлены на модерацию.")
            return redirect("web:campaign_detail", pk=campaign.pk)
        messages.success(request, "Кампания обновлена.")
        return redirect("web:campaign_detail", pk=campaign.pk)

    return render(request, "campaigns/create.html", {"form": form, "campaign": campaign})


@login_required
@require_POST
def campaign_submit(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk, advertiser=request.user)
    # Отклонённую кампанию после правок отправляют повторно; причину очищает одобрение.
    if campaign.status not in (Campaign.Status.DRAFT, Campaign.Status.REJECTED):
        messages.error(request, "На модерацию можно отправить только черновик или отклонённую кампанию.")
    else:
        source = (
            NotificationService.MODERATION_AFTER_REJECTION
            if campaign.status == Campaign.Status.REJECTED
            else NotificationService.MODERATION_FIRST
        )
        campaign.status = Campaign.Status.MODERATION
        campaign.save(update_fields=["status"])
        NotificationService.notify_campaign_moderation_requested(campaign, source)
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
    try:
        proposed_price = _parse_price(request.POST.get("proposed_price"))
    except ValueError:
        messages.error(request, "Укажите цену числом, например 150 000, или оставьте поле пустым.")
        return redirect("web:campaign_detail", pk=pk)
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
        if deals_in_cap(campaign) >= campaign.max_bloggers:
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


@login_required
def my_responses(request):
    """«Мои отклики» блогера: все его отклики, новые сверху."""
    from django.core.paginator import Paginator

    if request.user.is_staff or request.user.role != User.Role.BLOGGER:
        return _redirect_dashboard(request.user)
    qs = (
        CampaignResponse.objects.filter(blogger=request.user)
        .select_related("campaign", "platform")
        .order_by("-created_at")
    )
    page_obj = Paginator(qs, 20).get_page(request.GET.get("page", 1))
    return render(request, "campaigns/my_responses.html", {"responses": page_obj, "page_obj": page_obj})


def _answer_proposal(request, pk, accept):
    """Владелец принимает или отклоняет правки модератора (atomic + select_for_update)."""
    from django.db import transaction as db_transaction
    from django.utils import timezone

    campaign = get_object_or_404(Campaign, pk=pk, advertiser=request.user)
    with db_transaction.atomic():
        proposal = (
            CampaignEditProposal.objects.select_for_update()
            .filter(campaign=campaign, status=CampaignEditProposal.Status.PENDING).first()
        )
        if proposal is None or campaign.status != Campaign.Status.MODERATION:
            messages.error(request, "Нет предложений модератора, ожидающих ответа.")
            return redirect("web:campaign_detail", pk=pk)

        if accept:
            form = proposal_form(proposal)
            if not form.is_valid():
                problems = "; ".join(str(e) for errs in form.errors.values() for e in errs)
                messages.error(request, f"Правки нельзя применить — условия кампании изменились: {problems}")
                return redirect("web:campaign_detail", pk=pk)
            campaign = save_campaign_form(form)
            campaign.status = Campaign.Status.ACTIVE
            campaign.rejection_reason = ""
            campaign.save(update_fields=["status", "rejection_reason", "updated_at"])
            proposal.status = CampaignEditProposal.Status.ACCEPTED
        else:
            campaign.status = Campaign.Status.REJECTED
            campaign.rejection_reason = proposal.comment
            campaign.save(update_fields=["status", "rejection_reason", "updated_at"])
            proposal.status = CampaignEditProposal.Status.DECLINED
        proposal.responded_at = timezone.now()
        proposal.save(update_fields=["status", "responded_at"])

    NotificationService.notify_campaign_changes_answered(proposal.author, campaign, accepted=accept)
    if accept:
        messages.success(request, "Правки приняты — кампания активна и видна блогерам.")
    else:
        messages.success(request, "Правки отклонены. Исправьте кампанию сами и отправьте на модерацию.")
    return redirect("web:campaign_detail", pk=pk)


@login_required
@require_POST
def campaign_proposal_accept(request, pk):
    return _answer_proposal(request, pk, accept=True)


@login_required
@require_POST
def campaign_proposal_decline(request, pk):
    return _answer_proposal(request, pk, accept=False)
