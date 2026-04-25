import functools
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.billing.models import Transaction, Wallet, WithdrawalRequest
from apps.billing.services import BillingService
from apps.campaigns.models import Campaign
from apps.deals.models import Deal, DealStatusLog
from apps.notifications.service import NotificationService
from apps.platforms.models import Category, PermitDocument, Platform
from apps.users.models import User

from ..forms import CategoryForm
from .pages import _redirect_dashboard


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

    total_payments = (
        Transaction.objects.filter(type=Transaction.Type.PAYMENT)
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )
    total_earnings = (
        Transaction.objects.filter(type=Transaction.Type.EARNING)
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )
    platform_revenue = total_payments - total_earnings

    transaction_volume_month = (
        Transaction.objects.filter(created_at__gte=last_30)
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )

    top_advertisers = (
        Transaction.objects.filter(type=Transaction.Type.PAYMENT)
        .values("wallet__user__email")
        .annotate(total=Sum("amount"))
        .order_by("-total")[:5]
    )
    top_bloggers = (
        Transaction.objects.filter(type=Transaction.Type.EARNING)
        .values("wallet__user__email")
        .annotate(total=Sum("amount"))
        .order_by("-total")[:5]
    )

    context = {
        "campaigns_moderation": Campaign.objects.filter(status=Campaign.Status.MODERATION).count(),
        "platforms_pending": Platform.objects.filter(status=Platform.Status.PENDING).count(),
        "deals_disputed": Deal.objects.filter(status=Deal.Status.DISPUTED).count(),
        "withdrawals_pending": WithdrawalRequest.objects.filter(status=WithdrawalRequest.Status.PENDING).count(),
        "permits_pending": PermitDocument.objects.filter(status=PermitDocument.Status.PENDING).count(),
        "users_total": User.objects.count(),
        "users_active": User.objects.filter(status=User.Status.ACTIVE).count(),
        "new_users_month": User.objects.filter(date_joined__gte=last_30).count(),
        "deals_total": Deal.objects.count(),
        "deals_completed": Deal.objects.filter(status=Deal.Status.COMPLETED).count(),
        "platform_revenue": platform_revenue,
        "transaction_volume_month": transaction_volume_month,
        "top_advertisers": top_advertisers,
        "top_bloggers": top_bloggers,
    }
    return render(request, "admin_panel/dashboard.html", context)


@_staff_required
def admin_campaigns(request):
    campaigns = (
        Campaign.objects.filter(status=Campaign.Status.MODERATION)
        .select_related("advertiser", "category")
        .order_by("created_at")
    )
    return render(request, "admin_panel/campaigns.html", {"campaigns": campaigns})


@_staff_required
@require_POST
def admin_campaign_approve(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk)
    if campaign.status != Campaign.Status.MODERATION:
        messages.error(request, "Кампания не на модерации.")
        return redirect("web:admin_campaigns")
    campaign.status = Campaign.Status.ACTIVE
    campaign.rejection_reason = ""
    campaign.save(update_fields=["status", "rejection_reason", "updated_at"])
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
    reason = request.POST.get("reason", "").strip()
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
    deals = (
        Deal.objects.filter(status=Deal.Status.DISPUTED)
        .select_related("campaign", "blogger", "advertiser", "platform")
        .order_by("dispute_opened_at")
    )
    return render(request, "admin_panel/disputes.html", {"deals": deals})


@_staff_required
@require_POST
def admin_dispute_resolve(request, pk):
    """Admin resolves dispute: complete (pay blogger) or cancel (return to advertiser)."""
    deal = get_object_or_404(Deal, pk=pk, status=Deal.Status.DISPUTED)
    resolution = request.POST.get("resolution")  # "complete" or "cancel"
    comment = request.POST.get("comment", "").strip()

    if resolution not in ("complete", "cancel"):
        messages.error(request, "Укажите решение: complete или cancel.")
        return redirect("web:admin_disputes")

    from django.db import transaction as db_transaction
    with db_transaction.atomic():
        locked = Deal.objects.select_for_update().get(pk=pk)
        if locked.status != Deal.Status.DISPUTED:
            messages.error(request, "Сделка уже не в статусе спора.")
            return redirect("web:admin_disputes")

        locked.dispute_resolved_at = timezone.now()
        locked.dispute_resolution = comment

        if resolution == "complete":
            DealStatusLog.log(locked, Deal.Status.COMPLETED, changed_by=request.user,
                              comment=f"Досудебное урегулирование: оплата переведена блогеру по итогам рассмотрения. {comment}")
            BillingService.complete_deal_payment(locked)
            locked.status = Deal.Status.COMPLETED
            msg = "Досудебное урегулирование завершено — оплата переведена блогеру."
        else:
            DealStatusLog.log(locked, Deal.Status.CANCELLED, changed_by=request.user,
                              comment=f"Досудебное урегулирование: средства возвращены рекламодателю по итогам рассмотрения. {comment}")
            BillingService.release_funds(locked)
            locked.status = Deal.Status.CANCELLED
            msg = "Досудебное урегулирование завершено — средства возвращены рекламодателю."

        locked.save(update_fields=["status", "dispute_resolved_at", "dispute_resolution", "updated_at"])

    messages.success(request, msg)
    return redirect("web:admin_disputes")


@_staff_required
def admin_withdrawals(request):
    withdrawals = (
        WithdrawalRequest.objects.filter(status=WithdrawalRequest.Status.PENDING)
        .select_related("blogger")
        .order_by("created_at")
    )
    return render(request, "admin_panel/withdrawals.html", {"withdrawals": withdrawals})


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
    from django.db import transaction as db_transaction
    with db_transaction.atomic():
        wr = get_object_or_404(
            WithdrawalRequest.objects.select_for_update(),
            pk=pk, status=WithdrawalRequest.Status.PENDING,
        )
        wallet = Wallet.objects.select_for_update().get(user=wr.blogger)
        wallet.on_withdrawal -= wr.amount
        wallet.save(update_fields=["on_withdrawal", "updated_at"])
        wr.status = WithdrawalRequest.Status.COMPLETED
        wr.processed_at = timezone.now()
        wr.admin_comment = request.POST.get("comment", "").strip()
        wr.save(update_fields=["status", "processed_at", "admin_comment", "updated_at"])
    NotificationService.notify_withdrawal_approved(wr.blogger, wr.amount)
    messages.success(request, f"Выплата {wr.amount:,.0f} для {wr.blogger.email} подтверждена.")
    return redirect("web:admin_withdrawals")


@_staff_required
@require_POST
def admin_withdrawal_reject(request, pk):
    comment = request.POST.get("comment", "").strip()
    from django.db import transaction as db_transaction
    with db_transaction.atomic():
        wr = get_object_or_404(
            WithdrawalRequest.objects.select_for_update(),
            pk=pk, status=WithdrawalRequest.Status.PENDING,
        )
        BillingService.refund(wr)
        wr.status = WithdrawalRequest.Status.REJECTED
        wr.processed_at = timezone.now()
        wr.admin_comment = comment
        wr.save(update_fields=["status", "processed_at", "admin_comment", "updated_at"])
    NotificationService.notify_withdrawal_rejected(wr.blogger, wr.amount, comment)
    messages.success(request, f"Заявка отклонена, средства возвращены на баланс {wr.blogger.email}.")
    return redirect("web:admin_withdrawals")


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
