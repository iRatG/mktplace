from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from apps.billing.models import Transaction, Wallet, WithdrawalRequest
from apps.billing.services import BillingService
from apps.users.models import User


@login_required
def wallet_view(request):
    user = request.user
    wallet, _ = Wallet.objects.get_or_create(user=user)
    from django.core.paginator import Paginator
    txn_qs = wallet.transactions.order_by("-created_at")
    txn_page = Paginator(txn_qs, 20).get_page(request.GET.get("page", 1))
    transactions = txn_page

    withdrawal_submitted = False
    min_withdrawal = getattr(settings, "CURRENCY_MIN_WITHDRAWAL", 500)

    if request.method == "POST" and user.role == User.Role.BLOGGER:
        amount_str = request.POST.get("amount", "").strip()
        card = request.POST.get("card", "").strip()
        from decimal import Decimal as D, InvalidOperation
        try:
            amount = D(amount_str)
        except (InvalidOperation, ValueError):
            messages.error(request, "Некорректная сумма — введите число.")
            amount = None

        if amount is not None:
            if amount < D(str(min_withdrawal)):
                messages.error(request, f"Минимальная сумма вывода: {min_withdrawal:,} {getattr(settings, 'CURRENCY_SYMBOL', '')}.")
            elif amount > wallet.available_balance:
                messages.error(request, "Недостаточно средств на балансе.")
            elif not card:
                messages.error(request, "Укажите реквизиты для выплаты.")
            else:
                from django.db import transaction as db_transaction
                try:
                    with db_transaction.atomic():
                        wr = WithdrawalRequest.objects.create(
                            blogger=user,
                            amount=amount,
                            requisites={"type": "card", "details": card},
                        )
                        BillingService.process_withdrawal(wr)
                    messages.success(request, f"Заявка на вывод {amount:,.0f} {getattr(settings, 'CURRENCY_SYMBOL', '')} подана.")
                    return redirect("web:wallet")
                except ValueError as e:
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
        "withdrawal_submitted": withdrawal_submitted,
        "pending_withdrawals": pending_withdrawals,
        "min_withdrawal": min_withdrawal,
    })
