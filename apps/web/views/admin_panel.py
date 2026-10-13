import functools
from datetime import timedelta

from django.contrib import messages
from django.db.models import Exists, OuterRef
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.billing import metrics
from apps.billing.formatting import format_money
from apps.billing.models import WithdrawalRequest
from apps.billing.services import BillingService
from apps.campaigns.models import Campaign, CampaignEditProposal
from apps.campaigns.services import expired_error
from apps.campaigns.validation import permit_error
from apps.deals import services as transitions
from apps.deals.models import Deal
from apps.deals.services import TransitionError
from apps.notifications.service import NotificationService
from apps.platforms.models import Category, PermitDocument, Platform
from apps.users.models import User

from ..campaign_proposals import (
    CARD_TERMS_FIELDS, campaign_snapshot, changes_since_approval, compute_changes, describe, describe_changes,
    describe_terms, mark_approved,
)
from ..forms import CampaignForm, CategoryForm
from .pages import _redirect_dashboard


def _pending_platform_reviews():
    from apps.feedback.models import PlatformReview

    return PlatformReview.objects.filter(status=PlatformReview.Status.PENDING).count()


def _staff_required(view_func):
    """Decorator: allow only is_staff users, redirect others to dashboard."""
    @functools.wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("web:login")
        if not request.user.is_staff:
            messages.error(request, "Доступ запрещён.")
            return _redirect_dashboard(request.user)
        return view_func(request, *args, **kwargs)
    _wrapped.__name__ = view_func.__name__
    return _wrapped


@_staff_required
def admin_dashboard(request):
    """Дашборд администратора: операционные метрики + финансовая аналитика."""
    last_30 = timezone.now() - timedelta(days=30)

    platform_revenue = metrics.platform_revenue()
    deal_turnover_month = metrics.deal_turnover(since=last_30)
    top_advertisers = metrics.top_advertisers()
    top_bloggers = metrics.top_bloggers()

    context = {
        "campaigns_moderation": Campaign.objects.filter(status=Campaign.Status.MODERATION).count(),
        "platforms_pending": Platform.objects.filter(status=Platform.Status.PENDING).count(),
        "deals_disputed": Deal.objects.filter(status=Deal.Status.DISPUTED).count(),
        "withdrawals_pending": WithdrawalRequest.objects.filter(status=WithdrawalRequest.Status.PENDING).count(),
        "payouts_ready": sum(
            1 for p in WithdrawalRequest.objects.filter(status=WithdrawalRequest.Status.PENDING)
            .select_related("blogger").prefetch_related("deal__einvoices") if p.is_ready
        ),
        "permits_pending": PermitDocument.objects.filter(status=PermitDocument.Status.PENDING).count(),
        "reviews_pending": _pending_platform_reviews(),
        "users_total": User.objects.count(),
        "users_active": User.objects.filter(status=User.Status.ACTIVE).count(),
        "new_users_month": User.objects.filter(date_joined__gte=last_30).count(),
        "deals_total": Deal.objects.count(),
        "deals_completed": Deal.objects.filter(status=Deal.Status.COMPLETED).count(),
        "platform_revenue": platform_revenue,
        "deal_turnover_month": deal_turnover_month,
        "top_advertisers": top_advertisers,
        "top_bloggers": top_bloggers,
    }
    return render(request, "admin_panel/dashboard.html", context)


@_staff_required
def admin_campaigns(request):
    campaigns = (
        Campaign.objects.filter(status=Campaign.Status.MODERATION)
        .select_related("advertiser", "category")
        .annotate(has_pending_proposal=Exists(_pending_proposals(OuterRef("pk"))))
        .order_by("created_at")
    )
    return render(request, "admin_panel/campaigns.html", {"campaigns": campaigns})


def _pending_proposals(campaign):
    return CampaignEditProposal.objects.filter(campaign=campaign, status=CampaignEditProposal.Status.PENDING)


def _blocked_by_proposal(request, campaign):
    """Пока рекламодатель не ответил на правки, одобрять/отклонять нельзя (issue #6)."""
    if _pending_proposals(campaign).exists():
        messages.error(request, "Ждём ответа рекламодателя на предложенные правки — одобрить или отклонить пока нельзя.")
        return True
    return False


@_staff_required
def admin_campaign_detail(request, pk):
    """Карточка кампании для модерации: все параметры, формы — только для MODERATION."""
    campaign = get_object_or_404(
        Campaign.objects.select_related("advertiser", "category"), pk=pk
    )
    proposal = _pending_proposals(campaign).select_related("author").first()
    return render(request, "admin_panel/campaign_detail.html", {
        "campaign": campaign,
        "proposal": proposal,
        "proposal_rows": describe_changes(proposal) if proposal else [],
        "card_terms_rows": describe_terms(campaign_snapshot(campaign), CARD_TERMS_FIELDS),
        "permit_error": permit_error(campaign),
        **_since_approval_context(campaign, proposal),
    })


def _since_approval_context(campaign, proposal):
    """Повторная модерация: что изменилось с последнего одобрения (None — одобрения не было)."""
    if proposal or campaign.status != Campaign.Status.MODERATION:
        return {"since_approval_rows": None}
    changes = changes_since_approval(campaign)
    return {"since_approval_rows": None if changes is None else describe(changes)}


@_staff_required
def admin_campaign_propose(request, pk):
    """Модератор предлагает правки: форма кампании с текущими значениями + комментарий."""
    campaign = get_object_or_404(Campaign.objects.select_related("advertiser"), pk=pk)
    if campaign.status != Campaign.Status.MODERATION:
        messages.error(request, "Предложить правки можно только для кампании на модерации.")
        return redirect("web:admin_campaign_detail", pk=pk)
    if _pending_proposals(campaign).exists():
        messages.error(request, "У кампании уже есть предложение, ждём ответа рекламодателя.")
        return redirect("web:admin_campaign_detail", pk=pk)

    comment = request.POST.get("proposal_comment", "").strip()
    # Отдельный экземпляр: ModelForm при проверке меняет instance в памяти, а кампанию
    # до ответа рекламодателя трогать нельзя.
    form = CampaignForm(request.POST or None, instance=Campaign.objects.get(pk=pk))
    # Пустой комментарий — ошибка у поля, вместе с остальными ошибками формы (QA camp_test_3, шаг 3.3).
    comment_error = ""
    if request.method == "POST" and not comment:
        comment_error = "Напишите комментарий: рекламодатель увидит, зачем эти правки."
    if request.method == "POST" and form.is_valid() and not comment_error:
        changes = compute_changes(campaign, form)
        if not changes:
            messages.error(request, "Вы ничего не изменили — предлагать нечего.")
        else:
            CampaignEditProposal.objects.create(
                campaign=campaign, author=request.user, changes=changes, comment=comment,
            )
            NotificationService.notify_campaign_changes_proposed(campaign.advertiser, campaign)
            messages.success(request, "Правки отправлены рекламодателю. Ждём его ответа.")
            return redirect("web:admin_campaign_detail", pk=pk)

    return render(request, "campaigns/create.html", {
        "form": form, "campaign": campaign, "proposal_mode": True, "proposal_comment": comment,
        "proposal_comment_error": comment_error,
    })


@_staff_required
@require_POST
def admin_campaign_approve(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk)
    if campaign.status != Campaign.Status.MODERATION:
        messages.error(request, "Кампания не на модерации.")
        return redirect("web:admin_campaigns")
    if _blocked_by_proposal(request, campaign):
        return redirect("web:admin_campaign_detail", pk=pk)
    expired = expired_error(campaign)
    if expired:
        messages.error(request, f"{expired} Отклоните кампанию с этой причиной или предложите новые даты.")
        return redirect("web:admin_campaign_detail", pk=pk)
    permit = permit_error(campaign)
    if permit:
        messages.error(request, f"{permit} Отклоните кампанию с этой причиной.")
        return redirect("web:admin_campaign_detail", pk=pk)
    campaign.status = Campaign.Status.ACTIVE
    campaign.rejection_reason = ""
    mark_approved(campaign)
    campaign.save(update_fields=["status", "rejection_reason", "approved_snapshot", "updated_at"])
    NotificationService.notify_campaign_approved(campaign.advertiser, campaign)
    messages.success(request, f"Кампания «{campaign.name}» одобрена и опубликована.")
    return redirect("web:admin_campaigns")


@_staff_required
@require_POST
def admin_campaign_reject(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk)
    if campaign.status != Campaign.Status.MODERATION:
        messages.error(request, "Кампания не на модерации.")
        return redirect("web:admin_campaigns")
    if _blocked_by_proposal(request, campaign):
        return redirect("web:admin_campaign_detail", pk=pk)
    reason = request.POST.get("reason", "").strip()
    if not reason:
        messages.error(request, "Укажите причину отклонения — рекламодатель увидит её и исправит кампанию.")
        if request.POST.get("back") == "detail":
            return redirect("web:admin_campaign_detail", pk=campaign.pk)
        return redirect("web:admin_campaigns")
    campaign.status = Campaign.Status.REJECTED
    campaign.rejection_reason = reason
    campaign.save(update_fields=["status", "rejection_reason", "updated_at"])
    NotificationService.notify_campaign_rejected(campaign.advertiser, campaign)
    messages.success(request, f"Кампания «{campaign.name}» отклонена.")
    return redirect("web:admin_campaigns")


@_staff_required
def admin_platforms(request):
    platforms = (
        Platform.objects.filter(status=Platform.Status.PENDING)
        .select_related("blogger")
        .prefetch_related("categories")
        .order_by("created_at")
    )
    return render(request, "admin_panel/platforms.html", {"platforms": platforms})


@_staff_required
@require_POST
def admin_platform_approve(request, pk):
    platform = get_object_or_404(Platform, pk=pk)
    if platform.status != Platform.Status.PENDING:
        messages.error(request, "Площадка не на проверке.")
        return redirect("web:admin_platforms")
    platform.status = Platform.Status.APPROVED
    platform.rejection_reason = ""
    platform.save(update_fields=["status", "rejection_reason", "updated_at"])
    NotificationService.notify_platform_approved(platform.blogger, platform)
    messages.success(request, f"Площадка {platform.blogger.email} / {platform.get_social_type_display()} одобрена.")
    return redirect("web:admin_platforms")


@_staff_required
@require_POST
def admin_platform_reject(request, pk):
    platform = get_object_or_404(Platform, pk=pk)
    if platform.status != Platform.Status.PENDING:
        messages.error(request, "Площадка не на проверке.")
        return redirect("web:admin_platforms")
    reason = request.POST.get("reason", "").strip()
    platform.status = Platform.Status.REJECTED
    platform.rejection_reason = reason
    platform.save(update_fields=["status", "rejection_reason", "updated_at"])
    NotificationService.notify_platform_rejected(platform.blogger, platform)
    messages.success(request, f"Площадка отклонена.")
    return redirect("web:admin_platforms")


@_staff_required
def admin_disputes(request):
    from apps.deals.models import Claim

    deals = list(
        Deal.objects.filter(status=Deal.Status.DISPUTED)
        .select_related("campaign", "blogger", "advertiser", "platform")
        .prefetch_related("claims__files", "evidence")
        .order_by("dispute_opened_at")
    )
    for deal in deals:
        deal.current_claim = next((c for c in deal.claims.all() if c.status != Claim.Status.RESOLVED), None)
    return render(request, "admin_panel/disputes.html", {
        "deals": deals, "decisions": Claim.Decision.choices, "now": timezone.now(),
    })


@_staff_required
@require_POST
def admin_dispute_resolve(request, pk):
    """Решение сотрудника по претензии: всё исполнителю / всё рекламодателю / раздел / компенсация / доп. документы."""
    deal = get_object_or_404(Deal, pk=pk, status=Deal.Status.DISPUTED)
    decision = request.POST.get("decision", "")
    try:
        transitions.resolve_claim(deal.pk, request.user, decision, request.POST.get("comment", ""),
                                  request.POST.get("blogger_part"))
    except TransitionError as e:
        messages.error(request, str(e))
        return redirect("web:admin_disputes")
    messages.success(request, "Запрошены дополнительные документы." if decision == "extra_docs"
                     else "Решение по претензии принято.")
    return redirect("web:admin_disputes")


@_staff_required
def admin_withdrawals(request):
    """Реестр выплат (#60): выплаты по сделкам и остатки, причины блокировки, ЭСФ (#61), выгрузка в Excel.

    GET: status (pending по умолчанию, all — все), kind, deal, ready (1/0), date_from, date_to, demo=1 (показать
    демо-аккаунты), export=xlsx. Реквизиты — персональные данные: просмотр и выгрузка пишутся в журнал.
    """
    from apps.billing.esf import ALLOWED_STATUSES
    from apps.billing.exports import payouts_workbook, xlsx_response
    from apps.billing.models import EInvoice, Wallet
    from apps.users.pd_access import log_access

    from .finance import parse_date

    status = request.GET.get("status", WithdrawalRequest.Status.PENDING)
    kind = request.GET.get("kind", "")
    deal_id = request.GET.get("deal", "").strip()
    ready = request.GET.get("ready", "")
    show_demo = request.GET.get("demo") == "1"
    date_from = parse_date(request.GET.get("date_from"))
    date_to = parse_date(request.GET.get("date_to"))

    qs = (
        WithdrawalRequest.objects.select_related("blogger__blogger_profile", "deal__campaign")
        .prefetch_related("deal__einvoices").order_by("created_at")
    )
    if status != "all":
        qs = qs.filter(status=status)
    if kind:
        qs = qs.filter(kind=kind)
    if deal_id.isdigit():
        qs = qs.filter(deal_id=int(deal_id))
    if not show_demo:
        qs = qs.filter(blogger__is_demo=False)
    if date_from:
        qs = qs.filter(created_at__gte=metrics.period_bounds(date_from, date_from)[0])
    if date_to:
        qs = qs.filter(created_at__lt=metrics.period_bounds(date_to, date_to)[1])
    payouts = list(qs)
    if ready == "1":
        payouts = [p for p in payouts if p.is_ready]
    elif ready == "0":
        payouts = [p for p in payouts if not p.is_ready]

    if request.GET.get("export") == "xlsx":
        log_access(request, f"Выгрузка реестра выплат, строк: {len(payouts)}")
        return xlsx_response(payouts_workbook(payouts), f"payouts_{timezone.localdate():%Y-%m-%d}.xlsx")

    legacy_wallets = (
        Wallet.objects.filter(user__role=User.Role.BLOGGER, available_balance__gt=0).select_related("user")
        .order_by("-available_balance")
    )
    if not show_demo:
        legacy_wallets = legacy_wallets.filter(user__is_demo=False)
    platform_einvoices = (
        EInvoice.objects.filter(kind=EInvoice.Kind.PLATFORM, status=EInvoice.Status.TO_ISSUE)
        .select_related("deal__campaign", "deal__advertiser").order_by("created_at")
    )
    log_access(request, f"Реестр выплат, строк: {len(payouts)}")
    return render(request, "admin_panel/withdrawals.html", {
        "withdrawals": payouts,
        "ready_count": sum(1 for p in payouts if p.is_ready),
        "filters": {"status": status, "kind": kind, "deal": deal_id, "ready": ready, "demo": show_demo,
                    "date_from": date_from, "date_to": date_to},
        "status_choices": WithdrawalRequest.Status.choices,
        "kind_choices": WithdrawalRequest.Kind.choices,
        "performer_statuses": [(v, EInvoice.Status(v).label) for v in ALLOWED_STATUSES[EInvoice.Kind.PERFORMER]],
        "legacy_wallets": legacy_wallets,
        "platform_einvoices": platform_einvoices,
        "query": request.GET.urlencode(),
    })


@_staff_required
def admin_users(request):
    """Список пользователей с поиском и управлением статусом (Модуль 13).

    GET ?q=email — фильтрация по email (icontains).
    Позволяет блокировать/разблокировать пользователей через дочерние вьюхи.

    Контекст шаблона:
        users — QuerySet[User] (все или отфильтрованные), новые первые
        q     — строка поиска
    """
    q = request.GET.get("q", "").strip()
    users = User.objects.all().order_by("-date_joined")
    if q:
        users = users.filter(email__icontains=q)
    return render(request, "admin_panel/users.html", {"users": users, "q": q})


@_staff_required
@require_POST
def admin_withdrawal_approve(request, pk):
    wr = get_object_or_404(WithdrawalRequest, pk=pk, status=WithdrawalRequest.Status.PENDING)
    try:
        wr = BillingService.complete_withdrawal(wr, comment=request.POST.get("comment", "").strip())
    except ValueError as e:
        messages.error(request, f"Выплата не проведена: {e}")
        return _back_to_register(request)
    NotificationService.notify_withdrawal_approved(wr.blogger, wr.amount, deal=wr.deal)
    messages.success(request, f"Выплата {format_money(wr.amount)} для {wr.blogger.login_display} проведена.")
    return _back_to_register(request)


def _back_to_register(request):
    """Назад в реестр с теми же фильтрами (скрытое поле next, только свой путь)."""
    nxt = request.POST.get("next", "")
    if nxt.startswith("/panel/withdrawals/"):
        return redirect(nxt)
    return redirect("web:admin_withdrawals")


@_staff_required
@require_POST
def admin_withdrawal_reissue(request, pk):
    """Отклонённую выплату по сделке выпустить повторно (банк вернул перевод, реквизиты исправлены)."""
    wr = get_object_or_404(WithdrawalRequest, pk=pk, status=WithdrawalRequest.Status.REJECTED, deal__isnull=False)
    try:
        payout = BillingService.reissue_deal_payout(wr.deal)
    except ValueError as e:
        messages.error(request, f"Повторная выплата не создана: {e}")
        return _back_to_register(request)
    messages.success(request, f"Выплата {format_money(payout.amount)} по сделке #{wr.deal_id} снова в реестре.")
    return _back_to_register(request)


@_staff_required
@require_POST
def admin_withdrawal_legacy_balance(request, user_pk):
    """Остаток на доступном балансе исполнителя (до перехода на выплаты по сделке) — в реестр одной кнопкой."""
    from apps.billing.models import Wallet
    from apps.billing.payout_requisites import PayoutRequisitesError, create_withdrawal_request

    blogger = get_object_or_404(User, pk=user_pk, role=User.Role.BLOGGER)
    wallet = Wallet.objects.filter(user=blogger).first()
    if wallet is None or wallet.available_balance <= 0:
        messages.error(request, "На балансе нет остатка.")
        return _back_to_register(request)
    try:
        payout = create_withdrawal_request(blogger, wallet.available_balance)
    except (PayoutRequisitesError, ValueError) as e:
        messages.error(request, f"Остаток не поставлен в реестр: {e}")
        return _back_to_register(request)
    messages.success(request, f"Остаток {format_money(payout.amount)} для {blogger.login_display} — в реестре.")
    return _back_to_register(request)


@_staff_required
@require_POST
def admin_einvoice_update(request, pk):
    """Сотрудник вносит номер и статус ЭСФ (#61); отклонение и расхождение уведомляют сотрудников."""
    from apps.billing import esf

    try:
        einvoice = esf.set_status(
            pk, request.user, status=request.POST.get("status", ""), number=request.POST.get("number", ""),
            note=request.POST.get("note", ""),
        )
    except esf.EsfError as e:
        messages.error(request, str(e))
        return _back_to_register(request)
    messages.success(request, f"ЭСФ по сделке #{einvoice.deal_id}: {einvoice.get_status_display().lower()}.")
    return _back_to_register(request)


@_staff_required
@require_POST
def admin_withdrawal_reject(request, pk):
    comment = request.POST.get("comment", "").strip()
    wr = get_object_or_404(WithdrawalRequest, pk=pk, status=WithdrawalRequest.Status.PENDING)
    try:
        wr = BillingService.reject_withdrawal(wr, comment=comment)
    except ValueError as e:
        messages.error(request, f"Отказ не проведён: {e}")
        return _back_to_register(request)
    NotificationService.notify_withdrawal_rejected(wr.blogger, wr.amount, comment, deal=wr.deal)
    messages.success(request, f"Выплата отклонена, сумма возвращена на баланс {wr.blogger.login_display}.")
    return _back_to_register(request)


@_staff_required
@require_POST
def admin_user_block(request, pk):
    """Заблокировать пользователя (user.status = BLOCKED) (Модуль 13).

    POST /panel/users/<pk>/block/
    Нельзя заблокировать staff-аккаунт.
    Редирект → admin_users.
    """
    user = get_object_or_404(User, pk=pk)
    if user.is_staff:
        messages.error(request, "Нельзя заблокировать администратора.")
        return redirect("web:admin_users")
    user.status = User.Status.BLOCKED
    user.save(update_fields=["status"])
    messages.success(request, f"Пользователь {user.email} заблокирован.")
    return redirect("web:admin_users")


@_staff_required
@require_POST
def admin_user_unblock(request, pk):
    """Разблокировать пользователя (user.status = ACTIVE) (Модуль 13).

    POST /panel/users/<pk>/unblock/
    Редирект → admin_users.
    """
    user = get_object_or_404(User, pk=pk)
    user.status = User.Status.ACTIVE
    user.save(update_fields=["status"])
    messages.success(request, f"Пользователь {user.email} разблокирован.")
    return redirect("web:admin_users")


@_staff_required
def admin_categories(request):
    """Управление категориями платформ: список + создание (Модуль 13).

    GET  /panel/categories/ — список всех категорий + форма создания.
    POST /panel/categories/ — создать новую категорию (name + slug).

    Если name уже существует — ошибка (Category.name unique=True).
    Редирект после POST → admin_categories.

    Контекст шаблона:
        categories — QuerySet[Category]
        form       — CategoryForm
    """
    form = CategoryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        name = form.cleaned_data["name"]
        slug = form.cleaned_data["slug"]
        if Category.objects.filter(name=name).exists():
            messages.error(request, f"Категория «{name}» уже существует.")
        elif Category.objects.filter(slug=slug).exists():
            messages.error(request, f"Slug «{slug}» уже занят.")
        else:
            Category.objects.create(name=name, slug=slug)
            messages.success(request, f"Категория «{name}» добавлена.")
        return redirect("web:admin_categories")
    categories = Category.objects.all()
    return render(request, "admin_panel/categories.html", {
        "categories": categories,
        "form": form,
    })


@_staff_required
@require_POST
def admin_category_delete(request, pk):
    """Удалить категорию (Модуль 13).

    POST /panel/categories/<pk>/delete/
    Редирект → admin_categories.
    """
    cat = get_object_or_404(Category, pk=pk)
    name = cat.name
    cat.delete()
    messages.success(request, f"Категория «{name}» удалена.")
    return redirect("web:admin_categories")
