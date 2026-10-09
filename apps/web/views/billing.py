import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from apps.billing.formatting import format_money
from apps.billing.models import PayoutRequisites, Transaction, Wallet, WithdrawalRequest
from apps.billing.payout_requisites import (
    PayoutRequisitesError, approve_payout_requisites, create_withdrawal_request, current_requisites,
    expected_requisite_type, reject_payout_requisites, submit_payout_requisites,
)
from apps.billing.services import BillingService
from apps.users.models import User

from django.views.decorators.http import require_POST

from .admin_panel import _staff_required

@login_required
def wallet_view(request):
    user = request.user
    wallet, _ = Wallet.objects.get_or_create(user=user)
    from django.core.paginator import Paginator
    txn_qs = wallet.transactions.order_by("-created_at")
    txn_page = Paginator(txn_qs, 20).get_page(request.GET.get("page", 1))
    transactions = txn_page

    min_withdrawal = getattr(settings, "CURRENCY_MIN_WITHDRAWAL", 500)
    entered = {"amount": ""}
    requisites = current_requisites(user) if user.role == User.Role.BLOGGER else None

    if request.method == "POST" and user.role == User.Role.BLOGGER and user.is_demo:
        messages.error(request, "Вывод средств недоступен для демо-аккаунтов.")
        return redirect("web:wallet")
    if request.method == "POST" and user.role == User.Role.BLOGGER:
        amount_str = request.POST.get("amount", "").strip()
        entered = {"amount": amount_str}
        from decimal import Decimal as D, InvalidOperation
        try:
            amount = D(re.sub(r"\s", "", amount_str))  # «100 000» — пробелы между разрядами
            if not amount.is_finite():
                raise ValueError(amount_str)
        except (InvalidOperation, ValueError):
            messages.error(request, "Некорректная сумма — введите число.")
            amount = None

        if amount is not None:
            if amount < D(str(min_withdrawal)):
                messages.error(request, f"Минимальная сумма вывода: {format_money(min_withdrawal)} {getattr(settings, 'CURRENCY_SYMBOL', '')}.")
            elif amount > wallet.available_balance:
                messages.error(request, "Недостаточно средств на балансе.")
            else:
                try:
                    create_withdrawal_request(user, amount)
                    messages.success(request, f"Заявка на вывод {format_money(amount)} {getattr(settings, 'CURRENCY_SYMBOL', '')} подана.")
                    return redirect("web:wallet")
                except (ValueError, PayoutRequisitesError) as e:
                    messages.error(request, f"Ошибка: {e}")

    pending_withdrawals = []
    if user.role == User.Role.BLOGGER:
        pending_withdrawals = WithdrawalRequest.objects.filter(
            blogger=user, status=WithdrawalRequest.Status.PENDING
        ).order_by("-created_at")

    return render(request, "billing/wallet.html", {
        "wallet": wallet,
        "transactions": transactions,
        "page_obj": transactions,
        "pending_withdrawals": pending_withdrawals,
        "min_withdrawal": min_withdrawal,
        "min_withdrawal_text": format_money(min_withdrawal),
        "entered": entered,
        "requisites": requisites,
    })


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
