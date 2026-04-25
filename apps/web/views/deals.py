from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Avg
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.billing.services import BillingService
from apps.deals.models import ChatMessage, Deal, DealStatusLog, Review
from apps.notifications.service import NotificationService
from apps.profiles.models import BloggerProfile
from apps.users.models import User

from ..forms import ChatMessageForm, CreativeSubmitForm, ReviewForm


@login_required
def deal_list(request):
    from django.core.paginator import Paginator
    user = request.user
    if user.is_staff:
        qs = (
            Deal.objects.all()
            .select_related("campaign", "blogger", "advertiser", "platform")
            .order_by("-created_at")
        )
    elif user.role == User.Role.ADVERTISER:
        qs = (
            Deal.objects.filter(advertiser=user)
            .select_related("campaign", "blogger", "platform")
            .order_by("-created_at")
        )
    else:
        qs = (
            Deal.objects.filter(blogger=user)
            .select_related("campaign", "advertiser", "platform")
            .order_by("-created_at")
        )
    page_obj = Paginator(qs, 20).get_page(request.GET.get("page", 1))
    return render(request, "deals/list.html", {"deals": page_obj, "page_obj": page_obj})


@login_required
def deal_detail(request, pk):
    """Детальная страница сделки (Модуль 7).

    Доступ: рекламодатель или блогер участника сделки, либо staff.
    Контекст шаблона:
        deal            — объект Deal
        logs            — история статусов (DealStatusLog)
        can_review      — bool: рекламодатель может оставить отзыв
        existing_review — объект Review (если уже оставлен), иначе None
        review_form     — ReviewForm (только если can_review=True)
    """
    from django.db import transaction as _tx
    user = request.user
    if user.is_staff:
        deal = get_object_or_404(Deal, pk=pk)
    elif user.role == User.Role.ADVERTISER:
        deal = get_object_or_404(Deal, pk=pk, advertiser=user)
    else:
        deal = get_object_or_404(Deal, pk=pk, blogger=user)

    # Fallback auto-complete: if Celery is down, complete overdue CHECKING deals on page open
    if deal.status == Deal.Status.CHECKING:
        overdue_threshold = timezone.now() - timedelta(hours=72)
        if deal.updated_at <= overdue_threshold:
            try:
                with _tx.atomic():
                    deal_locked = Deal.objects.select_for_update().get(
                        pk=deal.pk, status=Deal.Status.CHECKING
                    )
                    deal_locked.status = Deal.Status.COMPLETED
                    deal_locked.save(update_fields=["status", "updated_at"])
                    DealStatusLog.log(
                        deal=deal_locked,
                        new_status=Deal.Status.COMPLETED,
                        comment="Auto-completed after 72h timeout (fallback).",
                    )
                    from apps.billing.services import BillingService
                    BillingService.complete_deal_payment(deal_locked)
                    deal = deal_locked
            except Deal.DoesNotExist:
                deal.refresh_from_db()

    logs = deal.status_logs.select_related("changed_by").order_by("created_at")

    can_review = False
    existing_review = None
    review_form = None
    if deal.status == Deal.Status.COMPLETED and not user.is_staff:
        try:
            existing_review = deal.review
        except Review.DoesNotExist:
            if user == deal.advertiser:
                window_open = timezone.now() - deal.updated_at < timedelta(days=7)
                if window_open:
                    can_review = True
                    review_form = ReviewForm()

    # Chat context (Sprint 6)
    chat_messages = deal.messages.select_related("sender").all()
    chat_form = ChatMessageForm()
    read_only_statuses = {Deal.Status.COMPLETED, Deal.Status.CANCELLED}
    can_send_message = (
        deal.status not in read_only_statuses
        and (user == deal.blogger or user == deal.advertiser or user.is_staff)
    )

    # CPA tracking link (Sprint 8) — create lazily for CPA deals
    from apps.deals.models import TrackingLink
    tracking_link = None
    campaign = deal.campaign
    if (
        campaign.payment_type == campaign.PaymentType.CPA
        and deal.status not in {Deal.Status.CANCELLED}
        and not user.is_staff
    ):
        tracking_link, _ = TrackingLink.objects.get_or_create(deal=deal)

    return render(request, "deals/detail.html", {
        "deal": deal,
        "logs": logs,
        "can_review": can_review,
        "existing_review": existing_review,
        "review_form": review_form,
        "chat_messages": chat_messages,
        "chat_form": chat_form,
        "can_send_message": can_send_message,
        "tracking_link": tracking_link,
    })


@login_required
@require_POST
def deal_submit_publication(request, pk):
    """Blogger submits publication URL → status CHECKING."""
    url = request.POST.get("publication_url", "").strip()
    if not url:
        messages.error(request, "Укажите ссылку на публикацию.")
        return redirect("web:deal_detail", pk=pk)
    if not (url.startswith("http://") or url.startswith("https://")):
        messages.error(request, "Ссылка должна начинаться с http:// или https://")
        return redirect("web:deal_detail", pk=pk)

    from django.db import transaction as db_transaction
    with db_transaction.atomic():
        deal = Deal.objects.select_for_update().filter(pk=pk, blogger=request.user).first()
        if deal is None:
            from django.http import Http404
            raise Http404
        if deal.status != Deal.Status.IN_PROGRESS:
            messages.error(request, "Добавить публикацию можно только для сделки «В работе».")
            return redirect("web:deal_detail", pk=pk)

        DealStatusLog.log(
            deal, Deal.Status.CHECKING,
            changed_by=request.user,
            comment=f"Публикация размещена: {url}",
        )
        deal.publication_url = url
        deal.publication_at = timezone.now()
        deal.status = Deal.Status.CHECKING
        deal.save(update_fields=["publication_url", "publication_at", "status", "updated_at"])

    messages.success(request, "Ссылка добавлена. Ожидайте подтверждения рекламодателя.")
    return redirect("web:deal_detail", pk=pk)


@login_required
@require_POST
def deal_confirm(request, pk):
    """Advertiser confirms publication → COMPLETED + payment."""
    from django.db import transaction as db_transaction

    with db_transaction.atomic():
        # select_for_update: lock the row to prevent double-confirm race condition
        deal = Deal.objects.select_for_update().filter(
            pk=pk, advertiser=request.user
        ).first()
        if deal is None:
            from django.http import Http404
            raise Http404

        if deal.status != Deal.Status.CHECKING:
            messages.error(request, "Подтвердить можно только сделку «На проверке».")
            return redirect("web:deal_detail", pk=pk)

        DealStatusLog.log(
            deal, Deal.Status.COMPLETED,
            changed_by=request.user,
            comment="Рекламодатель подтвердил публикацию. Оплата выполнена.",
        )
        BillingService.complete_deal_payment(deal)
        deal.status = Deal.Status.COMPLETED
        deal.last_distributed_at = timezone.now()
        deal.save(update_fields=["status", "last_distributed_at", "updated_at"])

    NotificationService.notify_deal_completed(deal.blogger, deal)
    messages.success(request, "Сделка завершена. Блогер получил оплату.")
    return redirect("web:deal_detail", pk=pk)


@login_required
@require_POST
def deal_cancel(request, pk):
    """Cancel deal → CANCELLED + release funds."""
    user = request.user
    # Blogger can only cancel before work starts (WAITING_PAYMENT).
    # IN_PROGRESS means funds are reserved and work is underway — only advertiser can cancel then.
    if user.role == User.Role.BLOGGER:
        cancellable = {Deal.Status.WAITING_PAYMENT}
    else:
        cancellable = {Deal.Status.WAITING_PAYMENT, Deal.Status.IN_PROGRESS}

    from django.db import transaction as db_transaction
    with db_transaction.atomic():
        if user.role == User.Role.ADVERTISER:
            deal = Deal.objects.select_for_update().filter(pk=pk, advertiser=user).first()
        else:
            deal = Deal.objects.select_for_update().filter(pk=pk, blogger=user).first()
        if deal is None:
            from django.http import Http404
            raise Http404
        if deal.status not in cancellable:
            messages.error(request, "Эту сделку нельзя отменить на текущем этапе.")
            return redirect("web:deal_detail", pk=pk)

        DealStatusLog.log(
            deal, Deal.Status.CANCELLED,
            changed_by=user,
            comment=f"Отменено пользователем ({user.email}).",
        )
        BillingService.release_funds(deal)
        deal.status = Deal.Status.CANCELLED
        deal.save(update_fields=["status", "updated_at"])

    NotificationService.notify_deal_cancelled(deal, cancelled_by=user)
    messages.success(request, "Сделка отменена. Средства возвращены рекламодателю.")
    return redirect("web:deal_list")


@login_required
@require_POST
def deal_send_message(request, pk):
    """Отправка сообщения в чат сделки (Модуль 7 / Sprint 6).

    Доступ: блогер или рекламодатель сделки, либо is_staff.
    Гард: COMPLETED и CANCELLED → чат только для чтения.
    Создаёт ChatMessage(deal, sender, text, file).
    """
    user = request.user

    # Получаем сделку с проверкой доступа
    if user.is_staff:
        deal = get_object_or_404(Deal, pk=pk)
    elif user.role == User.Role.ADVERTISER:
        deal = get_object_or_404(Deal, pk=pk, advertiser=user)
    else:
        deal = get_object_or_404(Deal, pk=pk, blogger=user)

    # Гард: завершённые и отменённые сделки — только чтение
    read_only_statuses = {Deal.Status.COMPLETED, Deal.Status.CANCELLED}
    if deal.status in read_only_statuses:
        messages.error(request, "Чат этой сделки доступен только для чтения.")
        return redirect("web:deal_detail", pk=pk)

    form = ChatMessageForm(request.POST, request.FILES)
    if form.is_valid():
        text = form.cleaned_data["text"].strip()
        file = form.cleaned_data.get("file")
        ChatMessage.objects.create(
            deal=deal,
            sender=user,
            text=text,
            file=file,
        )
    else:
        for error in form.errors.get("__all__", []):
            messages.error(request, error)

    return redirect("web:deal_detail", pk=pk)


@login_required
@require_POST
def deal_submit_creative(request, pk):
    """Blogger submits creative for approval → status ON_APPROVAL.

    Доступ: только блогер сделки.
    Статус: только IN_PROGRESS.
    Сохраняет: creative_text, creative_media, creative_submitted_at.
    Уведомляет рекламодателя.
    """
    from django.db import transaction as db_transaction

    deal = get_object_or_404(Deal, pk=pk, blogger=request.user)
    if deal.status != Deal.Status.IN_PROGRESS:
        messages.error(request, "Отправить креатив можно только для сделки «В работе».")
        return redirect("web:deal_detail", pk=pk)

    form = CreativeSubmitForm(request.POST, request.FILES)
    if not form.is_valid():
        for error in form.errors.get("__all__", []):
            messages.error(request, error)
        return redirect("web:deal_detail", pk=pk)

    text = form.cleaned_data["creative_text"].strip()
    media = form.cleaned_data.get("creative_media")

    with db_transaction.atomic():
        deal = Deal.objects.select_for_update().get(pk=pk)
        if deal.status != Deal.Status.IN_PROGRESS:
            messages.error(request, "Статус сделки изменился. Попробуйте ещё раз.")
            return redirect("web:deal_detail", pk=pk)

        DealStatusLog.log(
            deal, Deal.Status.ON_APPROVAL,
            changed_by=request.user,
            comment="Блогер отправил креатив на согласование.",
        )
        deal.creative_text = text
        if media:
            deal.creative_media = media
        deal.creative_submitted_at = timezone.now()
        deal.creative_rejection_reason = ""
        deal.status = Deal.Status.ON_APPROVAL
        deal.save(update_fields=[
            "creative_text", "creative_media", "creative_submitted_at",
            "creative_rejection_reason", "status", "updated_at",
        ])

    ChatMessage.objects.create(
        deal=deal,
        text="Блогер отправил креатив на согласование.",
        is_system=True,
    )
    NotificationService.notify_creative_submitted(deal.advertiser, deal)
    messages.success(request, "Креатив отправлен на согласование рекламодателю.")
    return redirect("web:deal_detail", pk=pk)


@login_required
@require_POST
def deal_approve_creative(request, pk):
    """Advertiser approves creative → status back to IN_PROGRESS.

    Доступ: только рекламодатель сделки.
    Статус: только ON_APPROVAL.
    Устанавливает creative_approved_at.
    Уведомляет блогера.
    """
    from django.db import transaction as db_transaction

    deal = get_object_or_404(Deal, pk=pk, advertiser=request.user)
    if deal.status != Deal.Status.ON_APPROVAL:
        messages.error(request, "Согласовать можно только сделку «На согласовании».")
        return redirect("web:deal_detail", pk=pk)

    with db_transaction.atomic():
        deal = Deal.objects.select_for_update().get(pk=pk)
        if deal.status != Deal.Status.ON_APPROVAL:
            messages.error(request, "Статус сделки изменился. Попробуйте ещё раз.")
            return redirect("web:deal_detail", pk=pk)

        DealStatusLog.log(
            deal, Deal.Status.IN_PROGRESS,
            changed_by=request.user,
            comment="Рекламодатель согласовал креатив.",
        )
        deal.creative_approved_at = timezone.now()
        deal.creative_rejection_reason = ""
        deal.status = Deal.Status.IN_PROGRESS
        deal.save(update_fields=[
            "creative_approved_at", "creative_rejection_reason", "status", "updated_at",
        ])

    ChatMessage.objects.create(
        deal=deal,
        text="Рекламодатель согласовал креатив. Можно публиковать!",
        is_system=True,
    )
    NotificationService.notify_creative_approved(deal.blogger, deal)
    messages.success(request, "Креатив согласован. Блогер может публиковать.")
    return redirect("web:deal_detail", pk=pk)


@login_required
@require_POST
def deal_reject_creative(request, pk):
    """Advertiser rejects creative → status back to IN_PROGRESS with rejection reason.

    Доступ: только рекламодатель сделки.
    Статус: только ON_APPROVAL.
    Сохраняет creative_rejection_reason.
    Уведомляет блогера.
    """
    from django.db import transaction as db_transaction

    deal = get_object_or_404(Deal, pk=pk, advertiser=request.user)
    if deal.status != Deal.Status.ON_APPROVAL:
        messages.error(request, "Отклонить можно только сделку «На согласовании».")
        return redirect("web:deal_detail", pk=pk)

    reason = request.POST.get("rejection_reason", "").strip()
    if not reason:
        messages.error(request, "Укажите причину отклонения.")
        return redirect("web:deal_detail", pk=pk)

    with db_transaction.atomic():
        deal = Deal.objects.select_for_update().get(pk=pk)
        if deal.status != Deal.Status.ON_APPROVAL:
            messages.error(request, "Статус сделки изменился. Попробуйте ещё раз.")
            return redirect("web:deal_detail", pk=pk)

        DealStatusLog.log(
            deal, Deal.Status.IN_PROGRESS,
            changed_by=request.user,
            comment=f"Рекламодатель отклонил креатив: {reason}",
        )
        deal.creative_rejection_reason = reason
        deal.status = Deal.Status.IN_PROGRESS
        deal.save(update_fields=["creative_rejection_reason", "status", "updated_at"])

    ChatMessage.objects.create(
        deal=deal,
        text=f"Рекламодатель отклонил креатив. Причина: {reason}",
        is_system=True,
    )
    NotificationService.notify_creative_rejected(deal.blogger, deal)
    messages.success(request, "Креатив отклонён. Блогер получил уведомление.")
    return redirect("web:deal_detail", pk=pk)


@login_required
@require_POST
def deal_review_submit(request, pk):
    """Отправить отзыв о сделке — только рекламодатель, только COMPLETED, окно 7 дней (Модуль 7).

    POST /deals/<pk>/review/

    Создаёт Review (author=advertiser, target=blogger). После создания
    пересчитывает BloggerProfile.rating как среднее всех полученных отзывов.

    Редиректы → deal_detail.
    """
    deal = get_object_or_404(Deal, pk=pk, advertiser=request.user, status=Deal.Status.COMPLETED)

    # Guard: one review per deal
    try:
        deal.review  # noqa: B018
        messages.error(request, "Отзыв по этой сделке уже оставлен.")
        return redirect("web:deal_detail", pk=pk)
    except Review.DoesNotExist:
        pass

    # Guard: 7-day window
    if timezone.now() - deal.updated_at > timedelta(days=7):
        messages.error(request, "Срок для оставления отзыва (7 дней) истёк.")
        return redirect("web:deal_detail", pk=pk)

    form = ReviewForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Некорректный отзыв. Убедитесь что оценка от 1 до 5.")
        return redirect("web:deal_detail", pk=pk)

    Review.objects.create(
        deal=deal,
        author=request.user,
        target=deal.blogger,
        rating=form.cleaned_data["rating"],
        text=form.cleaned_data["text"],
    )

    # Recalculate blogger rating
    try:
        profile = BloggerProfile.objects.get(user=deal.blogger)
        avg = Review.objects.filter(target=deal.blogger).aggregate(avg=Avg("rating"))["avg"] or 0
        profile.rating = round(avg, 2)
        profile.save(update_fields=["rating"])
    except BloggerProfile.DoesNotExist:
        pass

    messages.success(request, "Спасибо! Ваш отзыв сохранён.")
    return redirect("web:deal_detail", pk=pk)
