from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Avg
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.deals import services as transitions
from apps.deals.models import ChatMessage, Claim, Deal, Review
from apps.deals.services import TransitionError
from apps.profiles.models import BloggerProfile
from apps.users.models import User

from ..forms import ChatMessageForm, CreativeSubmitForm, ReviewForm


def _own_deal(request, pk, side=None):
    """Сделка пользователя (404 для чужих). side — «blogger» или «advertiser», если действие только одной стороны."""
    user = request.user
    if side == "blogger" or (side is None and user.role == User.Role.BLOGGER):
        return get_object_or_404(Deal, pk=pk, blogger=user)
    return get_object_or_404(Deal, pk=pk, advertiser=user)


@login_required
def deal_list(request):
    from django.core.paginator import Paginator
    user = request.user
    if user.is_staff:
        qs = (
            Deal.objects.all()
            .select_related(
                "campaign", "blogger", "advertiser", "platform",
                "blogger__blogger_profile", "advertiser__advertiser_profile",
            )
            .order_by("-created_at")
        )
    elif user.role == User.Role.ADVERTISER:
        qs = (
            Deal.objects.filter(advertiser=user)
            .select_related("campaign", "blogger", "platform", "blogger__blogger_profile")
            .order_by("-created_at")
        )
    else:
        qs = (
            Deal.objects.filter(blogger=user)
            .select_related("campaign", "advertiser", "platform", "advertiser__advertiser_profile")
            .order_by("-created_at")
        )
    status_filter = request.GET.get("status", "")
    if status_filter == "active":
        qs = qs.exclude(status__in=[Deal.Status.COMPLETED, Deal.Status.CANCELLED])
    elif status_filter in Deal.Status.values:
        qs = qs.filter(status=status_filter)
    else:
        status_filter = ""
    page_obj = Paginator(qs, 20).get_page(request.GET.get("page", 1))
    return render(request, "deals/list.html", {
        "deals": page_obj, "page_obj": page_obj, "status_filter": status_filter,
    })


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
    user = request.user
    if user.is_staff:
        deal = get_object_or_404(Deal, pk=pk)
    elif user.role == User.Role.ADVERTISER:
        deal = get_object_or_404(Deal, pk=pk, advertiser=user)
    else:
        deal = get_object_or_404(Deal, pk=pk, blogger=user)

    # Запасное автозавершение (если таймер не сработал): тот же переход, что у Celery — 72 ч после публикации.
    if deal.status == Deal.Status.CHECKING and transitions.checking_overdue(deal):
        try:
            transitions.complete(deal.pk, actor=None)
        except TransitionError:
            pass
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
                window_open = timezone.now() - (deal.last_distributed_at or deal.updated_at) < timedelta(days=7)
                if window_open:
                    can_review = True
                    review_form = ReviewForm()

    # Chat context (Sprint 6)
    chat_messages = deal.messages.select_related("sender__blogger_profile", "sender__advertiser_profile").all()
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

    # Перенос даты публикации по согласию: пока публикации нет и у сделки есть дата.
    can_reschedule = (
        not user.is_staff and deal.publication_date and deal.status in Deal.UNPUBLISHED_STATUSES
    )
    date_change = deal.pending_date_change if deal.status in Deal.UNPUBLISHED_STATUSES else None
    reschedule_calendar = None
    if can_reschedule and date_change is None:
        from apps.campaigns.validation import publication_calendar
        reschedule_calendar = publication_calendar(campaign)

    # Условия оферты, на которых заключена сделка (снимок на момент направления).
    from apps.campaigns.models import DirectOffer
    from apps.web.campaign_proposals import describe_terms

    offer = DirectOffer.objects.filter(deal=deal).only("terms").first()
    offer_terms_rows = describe_terms(offer.terms) if offer else []

    from apps.campaigns.models import EVIDENCE_CHOICES

    evidence_labels = dict(EVIDENCE_CHOICES)
    evidence_fields = [(kind, evidence_labels.get(kind, kind)) for kind in deal.evidence_required]

    return render(request, "deals/detail.html", {
        "deal": deal,
        "offer_terms_rows": offer_terms_rows,
        "evidence_fields": evidence_fields,
        "evidence_items": deal.evidence.all(),
        "claim": deal.open_claim or deal.claims.filter(status=Claim.Status.RESOLVED).first(),
        "can_claim": (not user.is_staff and user in (deal.blogger, deal.advertiser)
                      and deal.status in transitions.CLAIMABLE),
        "claim_subjects": Claim.Subject.choices,
        "unpublished_statuses": Deal.UNPUBLISHED_STATUSES,
        "claim_demands": Claim.Demand.choices,
        "can_reschedule": can_reschedule,
        "date_change": date_change,
        "reschedule_calendar": reschedule_calendar,
        "logs": logs,
        "can_review": can_review,
        "existing_review": existing_review,
        "review_form": review_form,
        "chat_messages": chat_messages,
        "chat_form": chat_form,
        "can_send_message": can_send_message,
        "tracking_link": tracking_link,
    })


def _evidence_files(files, deal):
    """Файлы доказательств из формы: поле evidence_<вид>, можно несколько файлов на вид."""
    return {kind: files.getlist(f"evidence_{kind}") for kind in deal.evidence_required}


@login_required
@require_POST
def deal_submit_publication(request, pk):
    deal = _own_deal(request, pk, "blogger")
    try:
        transitions.submit_publication(deal.pk, request.user, request.POST.get("publication_url", ""),
                                       evidence=_evidence_files(request.FILES, deal))
    except TransitionError as e:
        messages.error(request, str(e))
        return redirect("web:deal_detail", pk=pk)
    messages.success(request, "Ссылка добавлена. Ожидайте подтверждения рекламодателя.")
    return redirect("web:deal_detail", pk=pk)


@login_required
@require_POST
def deal_confirm(request, pk):
    try:
        deal = transitions.confirm_publication(_own_deal(request, pk, "advertiser").pk, request.user)
    except TransitionError as e:
        messages.error(request, str(e))
        return redirect("web:deal_detail", pk=pk)
    if deal.status == Deal.Status.COMPLETED:
        messages.success(request, "Сделка завершена. Блогер получил оплату.")
    else:
        messages.success(request, f"Публикация принята. Оплата блогеру — "
                                  f"{timezone.localtime(deal.payout_due):%d.%m.%Y %H:%M}, по окончании срока сохранения.")
    return redirect("web:deal_detail", pk=pk)


@login_required
@require_POST
def deal_cancel(request, pk):
    try:
        transitions.cancel(_own_deal(request, pk).pk, request.user)
    except TransitionError as e:
        messages.error(request, str(e))
        return redirect("web:deal_detail", pk=pk)
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
    """Блогер отправляет креатив на согласование → «На согласовании» (переход — apps/deals/services.py)."""
    deal = _own_deal(request, pk, "blogger")
    form = CreativeSubmitForm(request.POST, request.FILES)
    if not form.is_valid():
        for error in form.errors.get("__all__", []):
            messages.error(request, error)
        return redirect("web:deal_detail", pk=pk)
    try:
        transitions.submit_creative(
            deal.pk, request.user, form.cleaned_data["creative_text"].strip(), form.cleaned_data.get("creative_media"),
        )
    except TransitionError as e:
        messages.error(request, str(e))
        return redirect("web:deal_detail", pk=pk)
    messages.success(request, "Креатив отправлен на согласование рекламодателю.")
    return redirect("web:deal_detail", pk=pk)


@login_required
@require_POST
def deal_dispute(request, pk):
    """Сторона сделки подаёт претензию — основание, нарушенное условие, требование, доказательства."""
    post = request.POST
    try:
        transitions.open_claim(
            _own_deal(request, pk).pk, request.user,
            subject=post.get("subject", ""), violated_term=post.get("violated_term", ""),
            description=post.get("description", ""), demand=post.get("demand", ""),
            demand_details=post.get("demand_details", ""), links=post.get("links", ""),
            files=request.FILES.getlist("claim_files"),
        )
    except TransitionError as e:
        messages.error(request, str(e))
        return redirect("web:deal_detail", pk=pk)
    messages.success(request, "Претензия подана. Деньги депонированы до решения сотрудника.")
    return redirect("web:deal_detail", pk=pk)


@login_required
@require_POST
def deal_claim_materials(request, pk):
    """Объяснения второй стороны или дополнительные материалы к претензии."""
    try:
        transitions.add_claim_materials(_own_deal(request, pk).pk, request.user, request.POST.get("text", ""),
                                        request.FILES.getlist("claim_files"))
    except TransitionError as e:
        messages.error(request, str(e))
        return redirect("web:deal_detail", pk=pk)
    messages.success(request, "Материалы добавлены к претензии.")
    return redirect("web:deal_detail", pk=pk)


@login_required
@require_POST
def deal_approve_creative(request, pk):
    try:
        transitions.approve_creative(_own_deal(request, pk, "advertiser").pk, request.user)
    except TransitionError as e:
        messages.error(request, str(e))
        return redirect("web:deal_detail", pk=pk)
    messages.success(request, "Креатив согласован. Блогер может публиковать.")
    return redirect("web:deal_detail", pk=pk)


@login_required
@require_POST
def deal_reject_creative(request, pk):
    try:
        transitions.reject_creative(_own_deal(request, pk, "advertiser").pk, request.user, request.POST.get("rejection_reason", ""))
    except TransitionError as e:
        messages.error(request, str(e))
        return redirect("web:deal_detail", pk=pk)
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
    # 7 дней от завершения сделки (last_distributed_at ставит каждый путь завершения), а не от любого сохранения.
    if timezone.now() - (deal.last_distributed_at or deal.updated_at) > timedelta(days=7):
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


def _date_change_action(request, pk, func, ok):
    deal = _own_deal(request, pk)
    try:
        func(deal.pk, request.user)
    except TransitionError as e:
        messages.error(request, str(e))
        return redirect("web:deal_detail", pk=pk)
    messages.success(request, ok)
    return redirect("web:deal_detail", pk=pk)


@login_required
@require_POST
def deal_propose_publication_date(request, pk):
    from .campaigns import _parse_date

    new_date = _parse_date(request.POST.get("publication_date"))
    if new_date is None:
        messages.error(request, "Укажите новую дату публикации.")
        return redirect("web:deal_detail", pk=pk)
    return _date_change_action(
        request, pk, lambda deal_pk, user: transitions.propose_publication_date(deal_pk, user, new_date),
        "Предложение о переносе даты отправлено. Дата изменится, когда вторая сторона согласится.",
    )


@login_required
@require_POST
def deal_accept_publication_date(request, pk):
    return _date_change_action(request, pk, transitions.accept_publication_date, "Дата публикации перенесена.")


@login_required
@require_POST
def deal_decline_publication_date(request, pk):
    return _date_change_action(request, pk, transitions.decline_publication_date,
                               "Перенос отклонён, дата публикации прежняя.")


def _termination_action(request, pk, func, ok):
    try:
        func(_own_deal(request, pk).pk, request.user)
    except TransitionError as e:
        messages.error(request, str(e))
        return redirect("web:deal_detail", pk=pk)
    messages.success(request, ok)
    return redirect("web:deal_detail", pk=pk)


@login_required
@require_POST
def deal_propose_termination(request, pk):
    reason = request.POST.get("reason", "")
    return _termination_action(
        request, pk, lambda deal_pk, user: transitions.propose_termination(deal_pk, user, reason),
        "Предложение отправлено. Сделка прекратится, когда вторая сторона согласится.",
    )


@login_required
@require_POST
def deal_accept_termination(request, pk):
    return _termination_action(request, pk, transitions.accept_termination,
                               "Сделка прекращена по соглашению сторон.")


@login_required
@require_POST
def deal_decline_termination(request, pk):
    return _termination_action(request, pk, transitions.decline_termination, "Предложение отклонено, сделка продолжается.")
