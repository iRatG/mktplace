import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from apps.billing.formatting import format_money
from apps.billing.models import Transaction, Wallet, WithdrawalRequest
from apps.billing.services import BillingService
from apps.users.models import User

# Номер банковской карты для выплаты: ровно 16 цифр (QA camp_test_3, шаг 14). Пробелы и дефисы между
# группами допускаются при вводе и убираются; хранится только 16 цифр.
CARD_DIGITS = 16


def card_number_error(raw):
    """Ошибка номера карты или None; вторым значением — номер из одних цифр."""
    digits = re.sub(r"[\s-]", "", raw or "")
    if not digits:
        return "Укажите номер карты для выплаты.", ""
    if not digits.isdigit() or len(digits) != CARD_DIGITS:
        return f"Номер карты — {CARD_DIGITS} цифр.", ""
    return None, digits


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
    entered = {"amount": "", "card": ""}

    if request.method == "POST" and user.role == User.Role.BLOGGER and user.is_demo:
        messages.error(request, "Вывод средств недоступен для демо-аккаунтов.")
        return redirect("web:wallet")
    if request.method == "POST" and user.role == User.Role.BLOGGER:
        amount_str = request.POST.get("amount", "").strip()
        card_raw = request.POST.get("card", "").strip()
        entered = {"amount": amount_str, "card": card_raw}
        from decimal import Decimal as D, InvalidOperation
        try:
            amount = D(re.sub(r"\s", "", amount_str))  # «100 000» — пробелы между разрядами
            if not amount.is_finite():
                raise ValueError(amount_str)
        except (InvalidOperation, ValueError):
            messages.error(request, "Некорректная сумма — введите число.")
            amount = None
        card_error, card = card_number_error(card_raw)

        if amount is not None:
            if amount < D(str(min_withdrawal)):
                messages.error(request, f"Минимальная сумма вывода: {format_money(min_withdrawal)} {getattr(settings, 'CURRENCY_SYMBOL', '')}.")
            elif amount > wallet.available_balance:
                messages.error(request, "Недостаточно средств на балансе.")
            elif card_error:
                messages.error(request, card_error)
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
                    messages.success(request, f"Заявка на вывод {format_money(amount)} {getattr(settings, 'CURRENCY_SYMBOL', '')} подана.")
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
        "min_withdrawal_text": format_money(min_withdrawal),
        "entered": entered,
    })
