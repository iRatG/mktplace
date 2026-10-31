"""Счёт на оплату и возврат свободного остатка: кабинет рекламодателя и очереди сотрудника."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.billing import topups
from apps.billing.models import BalanceRefund, PaymentInvoice
from apps.billing.topups import TopUpError
from apps.users.models import User
from apps.users.pd_access import log_access

from ..forms import ConfirmPaymentForm, InvoiceForm, RefundDetailsForm, RefundRequestForm
from .admin_panel import _staff_required


def _advertiser_only(user):
    if user.role != User.Role.ADVERTISER or user.is_staff:
        raise Http404


def wallet_context(user):
    """Блок рекламодателя в кошельке."""
    account, mfo = topups.source_account(user)
    return {
        "invoice_form": InvoiceForm(),
        "invoice_error": topups.top_up_error(user),
        "invoices": PaymentInvoice.objects.filter(advertiser=user)[:10],
        "refund_form": RefundRequestForm(initial={"account": account, "mfo": mfo}),
        "refund_source": account,
        "refunds": BalanceRefund.objects.filter(advertiser=user)[:10],
    }


def _first_error(form):
    return next(iter(form.errors.values()))[0]


@login_required
@require_POST
def invoice_create(request):
    _advertiser_only(request.user)
    form = InvoiceForm(request.POST)
    if not form.is_valid():
        messages.error(request, _first_error(form))
        return redirect("web:wallet")
    try:
        invoice = topups.create_invoice(request.user, form.cleaned_data["amount"])
    except TopUpError as e:
        messages.error(request, str(e))
        return redirect("web:wallet")
    return redirect("web:invoice_print", pk=invoice.pk)


@login_required
def invoice_print(request, pk):
    invoice = get_object_or_404(PaymentInvoice.objects.select_related("advertiser__advertiser_profile"), pk=pk)
    if request.user.is_staff:
        log_access(request, f"Счёт {invoice.number}", subject=invoice.advertiser)
    elif invoice.advertiser_id != request.user.pk:
        raise Http404
    return render(request, "billing/invoice_print.html", {"invoice": invoice})


@login_required
@require_POST
def refund_create(request):
    _advertiser_only(request.user)
    form = RefundRequestForm(request.POST)
    if not form.is_valid():
        messages.error(request, _first_error(form))
        return redirect("web:wallet")
    data = form.cleaned_data
    try:
        topups.request_refund(request.user, data["refund_amount"], data["account"], data["mfo"])
        messages.success(request, "Заявка принята, сумма больше не доступна для кампаний.")
    except TopUpError as e:
        messages.error(request, str(e))
    return redirect("web:wallet")


@login_required
@require_POST
def refund_cancel(request, pk):
    _advertiser_only(request.user)
    refund = get_object_or_404(BalanceRefund, pk=pk, advertiser=request.user)
    try:
        topups.cancel_refund(refund, request.user)
        messages.success(request, "Заявка отозвана, сумма снова доступна.")
    except TopUpError as e:
        messages.error(request, str(e))
    return redirect("web:wallet")


@login_required
@require_POST
def refund_update(request, pk):
    _advertiser_only(request.user)
    refund = get_object_or_404(BalanceRefund, pk=pk, advertiser=request.user)
    form = RefundDetailsForm(request.POST)
    if not form.is_valid():
        messages.error(request, _first_error(form))
        return redirect("web:wallet")
    try:
        topups.update_details(refund, request.user, form.cleaned_data["account"], form.cleaned_data["mfo"])
        messages.success(request, "Сведения отправлены, срок считается заново.")
    except TopUpError as e:
        messages.error(request, str(e))
    return redirect("web:wallet")


@_staff_required
def admin_invoices(request):
    queue = list(
        PaymentInvoice.objects.exclude(status=PaymentInvoice.Status.PAID)
        .select_related("advertiser__advertiser_profile").order_by("-created_at")[:200]
    )
    log_access(request, f"Счета на оплату: {len(queue)}")
    return render(request, "admin_panel/invoices.html", {"queue": queue, "today": timezone.localdate()})


@_staff_required
@require_POST
def admin_invoice_confirm(request, pk):
    invoice = get_object_or_404(PaymentInvoice, pk=pk)
    form = ConfirmPaymentForm(request.POST)
    if not form.is_valid():
        messages.error(request, _first_error(form))
        return redirect("web:admin_invoices")
    data = form.cleaned_data
    try:
        topups.confirm_payment(invoice, request.user, data["amount"], data["received_on"], data["account"], data["mfo"])
        messages.success(request, f"Счёт {invoice.number}: баланс пополнен.")
    except TopUpError as e:
        messages.error(request, str(e))
    return redirect("web:admin_invoices")


@_staff_required
def admin_refunds(request):
    open_statuses = (BalanceRefund.Status.REQUESTED, BalanceRefund.Status.NEEDS_INFO, BalanceRefund.Status.SENT)
    queue = list(BalanceRefund.objects.filter(status__in=open_statuses).select_related("advertiser").order_by("due_at"))
    for refund in queue:
        refund.source_account = topups.source_account(refund.advertiser)[0]
    log_access(request, f"Возвраты остатка: {len(queue)}")
    return render(request, "admin_panel/refunds.html", {"queue": queue})


@_staff_required
@require_POST
def admin_refund_action(request, pk):
    refund = get_object_or_404(BalanceRefund, pk=pk)
    action = request.POST.get("action")
    note = request.POST.get("note", "")
    try:
        if action == "sent":
            topups.mark_refund_sent(refund, request.user)
        elif action == "done":
            topups.complete_refund(refund, request.user)
        elif action == "info":
            topups.ask_for_details(refund, request.user, note)
        elif action == "reject":
            topups.reject_refund(refund, request.user, note)
        else:
            raise TopUpError("Неизвестное действие.")
        messages.success(request, "Заявка обновлена.")
    except TopUpError as e:
        messages.error(request, str(e))
    return redirect("web:admin_refunds")
