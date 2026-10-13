from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from apps.billing.formatting import format_money
from apps.billing.models import PayoutRequisites, Transaction, Wallet, WithdrawalRequest
from apps.billing.payout_requisites import (
    PayoutRequisitesError, approve_payout_requisites, current_requisites,
    expected_requisite_type, reject_payout_requisites, submit_payout_requisites,
)
from apps.users.models import User
from apps.users.pd_access import log_access

from django.views.decorators.http import require_POST

from .admin_panel import _staff_required

@login_required
def wallet_view(request):
    """Кошелёк. У исполнителя формы вывода нет (с 13.10.2026): выплаты формируются по сделкам, здесь — их состояние."""
    user = request.user
    wallet, _ = Wallet.objects.get_or_create(user=user)
    from django.core.paginator import Paginator
    txn_qs = wallet.transactions.order_by("-created_at")
    txn_page = Paginator(txn_qs, 20).get_page(request.GET.get("page", 1))
    transactions = txn_page

    requisites = current_requisites(user) if user.role == User.Role.BLOGGER else None
    payouts = []
    expected = None
    paid_out = None
    if user.role == User.Role.BLOGGER:
        from apps.billing import metrics

        payouts = list(
            WithdrawalRequest.objects.filter(blogger=user).select_related("deal__campaign")
            .prefetch_related("deal__einvoices").order_by("-created_at")[:50]
        )
        expected = _expected_payouts(user)
        paid_out = metrics.blogger_paid_out(user)

    budgets = None
    if user.role == User.Role.ADVERTISER:
        from apps.campaigns.validation import advertiser_budgets

        budgets = advertiser_budgets(user)

    return render(request, "billing/wallet.html", {
        "budgets": budgets,
        "expected": expected,
        "wallet": wallet,
        "transactions": transactions,
        "page_obj": transactions,
        "payouts": payouts,
        "pending_payouts": [p for p in payouts if p.status == WithdrawalRequest.Status.PENDING],
        "paid_out": paid_out,
        "legacy_available": user.role == User.Role.BLOGGER and wallet.available_balance > 0,
        "requisites": requisites,
    })


def _expected_payouts(blogger):
    """Будущие выплаты блогеру по сделкам в работе: сколько получит и когда (если публикация уже есть)."""
    from apps.deals.models import Deal

    deals = list(
        Deal.objects.filter(blogger=blogger).exclude(status__in=(Deal.Status.COMPLETED, Deal.Status.CANCELLED))
        .select_related("campaign").order_by("created_at")
    )
    return {"deals": deals, "total": sum((d.blogger_earning for d in deals), Decimal("0"))}


@login_required
def payout_requisites_view(request):
    """Подать заявку на реквизиты выплаты и посмотреть историю (T7/#39)."""
    user = request.user
    if user.role != User.Role.BLOGGER:
        messages.error(request, "Доступно только блогерам.")
        return redirect("web:landing")

    if request.method == "POST":
        try:
            submit_payout_requisites(
                user,
                card_number=request.POST.get("card_number", ""),
                card_holder_name=request.POST.get("card_holder_name", ""),
                account_number=request.POST.get("account_number", ""),
                bank_mfo=request.POST.get("bank_mfo", ""),
                bank_inn=request.POST.get("bank_inn", ""),
                bank_name=request.POST.get("bank_name", ""),
            )
            messages.success(request, "Заявка на реквизиты отправлена на проверку.")
            return redirect("web:payout_requisites")
        except PayoutRequisitesError as e:
            messages.error(request, str(e))

    applications = PayoutRequisites.objects.filter(user=user).order_by("-created_at")
    return render(request, "billing/payout_requisites.html", {
        "applications": applications,
        "has_pending": applications.filter(status=PayoutRequisites.Status.PENDING).exists(),
        "requisite_type": expected_requisite_type(user),
    })


@_staff_required
def admin_payout_requisites(request):
    applications = (
        PayoutRequisites.objects.filter(status=PayoutRequisites.Status.PENDING)
        .select_related("user", "user__blogger_profile")
        .order_by("created_at")
    )
    log_access(request, f"Очередь реквизитов выплаты, заявок: {len(applications)}")
    return render(request, "admin_panel/payout_requisites.html", {"applications": applications})


@_staff_required
@require_POST
def admin_payout_requisites_approve(request, pk):
    try:
        approve_payout_requisites(pk, request.user)
        messages.success(request, "Реквизиты подтверждены.")
    except PayoutRequisitesError as e:
        messages.error(request, str(e))
    return redirect("web:admin_payout_requisites")


@_staff_required
@require_POST
def admin_payout_requisites_reject(request, pk):
    try:
        reject_payout_requisites(pk, request.user, request.POST.get("reason", ""))
        messages.success(request, "Заявка отклонена.")
    except PayoutRequisitesError as e:
        messages.error(request, str(e))
    return redirect("web:admin_payout_requisites")
