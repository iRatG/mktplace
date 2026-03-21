from django.conf import settings
from django.contrib import messages
from django.utils import timezone
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.billing.models import Transaction, Wallet, WithdrawalRequest
from apps.billing.services import BillingService
from apps.campaigns.models import Campaign
from apps.campaigns.models import Response as CampaignResponse
from apps.deals.models import Deal, DealStatusLog
from apps.platforms.models import Platform
from apps.users.models import PasswordResetToken, User
from apps.users.tasks import send_password_reset_email

from .forms import (
    CampaignForm,
    LoginForm,
    PasswordResetConfirmForm,
    PasswordResetRequestForm,
    RegisterForm,
)


# ── Auth ──────────────────────────────────────────────────────────────────────

def login_view(request):
    if request.user.is_authenticated:
        return _redirect_dashboard(request.user)

    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].lower()
        password = form.cleaned_data["password"]

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            form.add_error(None, "Неверный email или пароль.")
            return render(request, "auth/login.html", {"form": form})

        if user.is_blocked:
            form.add_error(None, "Аккаунт заблокирован. Обратитесь в поддержку.")
            return render(request, "auth/login.html", {"form": form})

        if not user.check_password(password):
            user.increment_login_attempts()
            form.add_error(None, "Неверный email или пароль.")
            return render(request, "auth/login.html", {"form": form})

        if not user.is_email_confirmed:
            form.add_error(None, "Подтвердите email перед входом.")
            return render(request, "auth/login.html", {"form": form})

        user.reset_login_attempts()
        login(request, user)
        return _redirect_dashboard(user)

    return render(request, "auth/login.html", {"form": form})


def register_view(request):
    if request.user.is_authenticated:
        return _redirect_dashboard(request.user)

    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = User.objects.create_user(
            email=form.cleaned_data["email"],
            password=form.cleaned_data["password1"],
            role=form.cleaned_data["role"],
        )
        # Send confirmation email via Celery
        from apps.users.tasks import send_confirmation_email
        send_confirmation_email.delay(user.pk)
        messages.success(
            request,
            "Аккаунт создан! Проверьте почту и подтвердите email.",
        )
        return redirect("web:login")

    return render(request, "auth/register.html", {"form": form})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("web:login")


def email_confirm_view(request, token):
    from apps.users.models import EmailConfirmationToken as ECToken
    try:
        tok = ECToken.objects.get(token=token)
    except ECToken.DoesNotExist:
        return render(request, "auth/email_confirm_done.html", {"success": False})

    if not tok.is_valid:
        return render(request, "auth/email_confirm_done.html", {"success": False})

    tok.mark_used()
    user = tok.user
    user.is_email_confirmed = True
    user.status = User.Status.ACTIVE
    user.save(update_fields=["is_email_confirmed", "status"])
    return render(request, "auth/email_confirm_done.html", {"success": True})


def password_reset_request_view(request):
    sent = False
    form = PasswordResetRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].lower()
        try:
            user = User.objects.get(email=email)
            send_password_reset_email.delay(user.pk)
        except User.DoesNotExist:
            pass  # Don't reveal if user exists
        sent = True

    return render(request, "auth/password_reset_request.html", {"form": form, "sent": sent})


def password_reset_confirm_view(request, token):
    try:
        tok = PasswordResetToken.objects.get(token=token)
    except PasswordResetToken.DoesNotExist:
        return render(request, "auth/password_reset_confirm.html", {"invalid_token": True})

    if not tok.is_valid:
        return render(request, "auth/password_reset_confirm.html", {"invalid_token": True})

    form = PasswordResetConfirmForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        tok.user.set_password(form.cleaned_data["password1"])
        tok.user.save(update_fields=["password"])
        tok.mark_used()
        messages.success(request, "Пароль изменён. Войдите с новым паролем.")
        return redirect("web:login")

    return render(request, "auth/password_reset_confirm.html", {"form": form, "invalid_token": False})


# ── Dashboards ────────────────────────────────────────────────────────────────

@login_required
def advertiser_dashboard(request):
    user = request.user
    wallet = getattr(user, "wallet", None)
    campaigns_qs = Campaign.objects.filter(advertiser=user)
    recent_campaigns = campaigns_qs.order_by("-created_at")[:5]

    context = {
        "wallet": wallet,
        "campaigns_count": campaigns_qs.count(),
        "active_campaigns_count": campaigns_qs.filter(status=Campaign.Status.ACTIVE).count(),
        "pending_responses_count": CampaignResponse.objects.filter(
            campaign__advertiser=user, status=CampaignResponse.Status.PENDING
        ).count(),
        "active_deals_count": Deal.objects.filter(
            advertiser=user
        ).exclude(status__in=[Deal.Status.COMPLETED, Deal.Status.CANCELLED]).count(),
        "recent_campaigns": recent_campaigns,
    }
    return render(request, "dashboard/advertiser.html", context)


@login_required
def blogger_dashboard(request):
    user = request.user
    wallet = getattr(user, "wallet", None)
    active_deals = Deal.objects.filter(blogger=user).exclude(
        status__in=[Deal.Status.COMPLETED, Deal.Status.CANCELLED]
    ).select_related("campaign", "platform")[:10]

    context = {
        "wallet": wallet,
        "my_responses_count": CampaignResponse.objects.filter(blogger=user).count(),
        "active_deals_count": active_deals.count(),
        "completed_deals_count": Deal.objects.filter(
            blogger=user, status=Deal.Status.COMPLETED
        ).count(),
        "active_deals": active_deals,
        "has_platforms": Platform.objects.filter(blogger=user).exists(),
    }
    return render(request, "dashboard/blogger.html", context)


# ── Campaigns ─────────────────────────────────────────────────────────────────

@login_required
def campaign_list(request):
    user = request.user
    if user.role == User.Role.ADVERTISER:
        campaigns = Campaign.objects.filter(advertiser=user).select_related("category")
    else:
        campaigns = Campaign.objects.filter(status=Campaign.Status.ACTIVE).select_related("category")
    return render(request, "campaigns/list.html", {"campaigns": campaigns})


@login_required
def campaign_detail(request, pk):
    user = request.user
    if user.role == User.Role.ADVERTISER:
        campaign = get_object_or_404(Campaign, pk=pk, advertiser=user)
        responses = campaign.responses.select_related("blogger", "platform").order_by("-created_at")
        context = {
            "campaign": campaign,
            "is_owner": True,
            "responses": responses,
        }
    else:
        campaign = get_object_or_404(Campaign, pk=pk)
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
        return redirect("web:advertiser_dashboard")

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
    if campaign.status not in (Campaign.Status.DRAFT, Campaign.Status.REJECTED):
        messages.error(request, "Редактировать можно только черновики и отклонённые кампании.")
        return redirect("web:campaign_detail", pk=pk)

    form = CampaignForm(request.POST or None, instance=campaign)
    if request.method == "POST" and form.is_valid():
        campaign = form.save(commit=False)
        campaign.content_types = form.cleaned_data.get("content_types", [])
        campaign.allowed_socials = form.cleaned_data.get("allowed_socials", [])
        campaign.save()
        messages.success(request, "Кампания обновлена.")
        return redirect("web:campaign_detail", pk=campaign.pk)

    return render(request, "campaigns/create.html", {"form": form, "campaign": campaign})


@login_required
@require_POST
def campaign_submit(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk, advertiser=request.user)
    if campaign.status != Campaign.Status.DRAFT:
        messages.error(request, "Только черновики можно отправить на модерацию.")
    else:
        campaign.status = Campaign.Status.MODERATION
        campaign.save(update_fields=["status"])
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
    proposed_price = request.POST.get("proposed_price") or None
    message = request.POST.get("message", "")

    platform = get_object_or_404(Platform, pk=platform_id, blogger=request.user)

    CampaignResponse.objects.create(
        blogger=request.user,
        campaign=campaign,
        platform=platform,
        content_type=content_type,
        proposed_price=proposed_price,
        message=message,
    )
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
        active_deals_count = Deal.objects.filter(
            campaign=campaign,
            status__in=[
                Deal.Status.IN_PROGRESS,
                Deal.Status.CHECKING,
                Deal.Status.ON_APPROVAL,
                Deal.Status.WAITING_PUBLICATION,
                Deal.Status.COMPLETED,
            ],
        ).count()
        if active_deals_count >= campaign.max_bloggers:
            messages.error(request, f"Достигнут лимит блогеров для кампании ({campaign.max_bloggers}).")
            return redirect("web:campaign_detail", pk=campaign.pk)

    amount = resp.proposed_price or campaign.fixed_price
    if not amount:
        messages.error(request, "Не удалось определить сумму сделки.")
        return redirect("web:campaign_detail", pk=campaign.pk)

    try:
        with db_transaction.atomic():
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
            deal.status = Deal.Status.IN_PROGRESS
            deal.save(update_fields=["status"])
            DealStatusLog.log(deal, Deal.Status.IN_PROGRESS, changed_by=request.user, comment="Accepted via web.")
        messages.success(request, f"Отклик принят. Сделка #{deal.pk} создана.")
    except ValueError as e:
        messages.error(request, f"Недостаточно средств: {e}")

    return redirect("web:campaign_detail", pk=campaign.pk)


@login_required
@require_POST
def response_reject(request, pk):
    resp = get_object_or_404(CampaignResponse, pk=pk, campaign__advertiser=request.user)
    if resp.status == CampaignResponse.Status.PENDING:
        resp.status = CampaignResponse.Status.REJECTED
        resp.save(update_fields=["status"])
        messages.success(request, "Отклик отклонён.")
    return redirect("web:campaign_detail", pk=resp.campaign_id)


@login_required
def platform_add(request):
    messages.info(request, "Добавление площадок доступно через API.")
    return redirect("web:blogger_dashboard")


# ── Landing & Static pages ────────────────────────────────────────────────────

def landing(request):
    if request.user.is_authenticated:
        return _redirect_dashboard(request.user)

    context = {
        "platforms": [
            ("ВКонтакте", "🔵", "blue"),
            ("Telegram", "✈️", "sky"),
            ("YouTube", "▶️", "red"),
            ("Instagram", "📸", "pink"),
            ("TikTok", "🎵", "slate"),
            ("Яндекс.Дзен", "🟡", "yellow"),
        ],
        "advertiser_steps": [
            {"title": "Создайте кампанию", "desc": "Опишите продукт, требования к контенту, формат, бюджет — сохраняется как черновик"},
            {"title": "Пройдите модерацию", "desc": "Администраторы проверят кампанию и опубликуют в ленте для блогеров"},
            {"title": "Выберите блогера", "desc": "Смотрите отклики, оценивайте площадки, принимайте или отклоняйте"},
            {"title": "Согласуйте и подтвердите", "desc": "Проверьте публикацию и подтвердите — деньги поступят блогеру"},
        ],
        "blogger_steps": [
            {"title": "Добавьте площадку", "desc": "ВКонтакте, Telegram, YouTube, Instagram, TikTok или Яндекс.Дзен"},
            {"title": "Пройдите проверку", "desc": "Администраторы верифицируют площадку (до 48ч)"},
            {"title": "Откликнитесь на кампанию", "desc": "Выберите подходящую кампанию, предложите цену, напишите сообщение"},
            {"title": "Получите оплату", "desc": "Деньги заморожены с начала — гарантированно поступят после публикации"},
        ],
        "advertiser_features": [
            {"icon": "🔒", "title": "Эскроу-защита", "desc": "Средства списываются только после вашего подтверждения публикации"},
            {"icon": "✏️", "title": "Согласование контента", "desc": "Просматривайте и правьте материал до выхода — до 3 итераций"},
            {"icon": "🎯", "title": "Fixed и CPA", "desc": "Фиксированная оплата за пост или за реальный результат"},
            {"icon": "📊", "title": "Аналитика", "desc": "Отслеживайте расход бюджета, статусы сделок и эффективность"},
        ],
        "blogger_features": [
            {"icon": "✅", "title": "100% гарантия оплаты", "desc": "Деньги заморожены до старта — вас никто не обманет"},
            {"icon": "⏱", "title": "Авто-завершение 72ч", "desc": "Если рекламодатель молчит после публикации — деньги ваши автоматически"},
            {"icon": "💳", "title": "Вывод на карту", "desc": f"От {getattr(settings, 'CURRENCY_MIN_WITHDRAWAL', 500):,} {getattr(settings, 'CURRENCY_SYMBOL', '₽')}, обработка в течение 3 рабочих дней"},
            {"icon": "📱", "title": "Несколько площадок", "desc": "Добавляйте сколько угодно аккаунтов в разных соцсетях"},
        ],
        "faq_items": [
            {"q": "Сколько стоит использование платформы?", "a": "Регистрация бесплатна. Платформа берёт 15% комиссию только с успешно завершённых сделок — вычитается из выплаты блогеру."},
            {"q": "Как защищены деньги рекламодателя?", "a": "При создании сделки нужная сумма замораживается на счёте рекламодателя. Он не может потратить её на другое. Блогеру деньги поступают только после подтверждения."},
            {"q": "Что если рекламодатель не отвечает?", "a": "Если в течение 72 часов после публикации рекламодатель не ответил — сделка завершается автоматически и деньги зачисляются блогеру."},
            {"q": "Какие соцсети поддерживаются?", "a": "ВКонтакте, Telegram, YouTube, Instagram, TikTok и Яндекс.Дзен. Один блогер может добавить несколько площадок."},
            {"q": "Как вывести заработанные деньги?", "a": f"В разделе «Кошелёк» подайте заявку на вывод (от {getattr(settings, 'CURRENCY_MIN_WITHDRAWAL', 500):,} {getattr(settings, 'CURRENCY_SYMBOL', '₽')}). Обрабатывается в течение 3 рабочих дней."},
            {"q": "Что такое CPA-кампания?", "a": "Оплата за результат: установку приложения, регистрацию или покупку. Система генерирует уникальную трекинговую ссылку для каждого блогера."},
        ],
    }
    return render(request, "landing.html", context)


def faq(request):
    deal_statuses = [
        ("Ожидает оплаты", "bg-yellow-100 text-yellow-700", "Сделка создана, рекламодатель должен пополнить баланс"),
        ("В работе", "bg-blue-100 text-blue-700", "Средства зарезервированы, блогер приступил к работе"),
        ("На согласовании", "bg-purple-100 text-purple-700", "Блогер загрузил черновик, ждёт одобрения рекламодателя"),
        ("Ожидает публикации", "bg-indigo-100 text-indigo-700", "Креатив одобрен, блогер публикует контент"),
        ("Опубликовано", "bg-cyan-100 text-cyan-700", "Блогер опубликовал и прикрепил ссылку"),
        ("На проверке", "bg-orange-100 text-orange-700", "Рекламодатель проверяет публикацию (72ч)"),
        ("Завершена", "bg-green-100 text-green-700", "Публикация подтверждена, деньги переведены блогеру"),
        ("Оспорена", "bg-red-100 text-red-700", "Открыт спор, администратор рассматривает ситуацию"),
        ("Отменена", "bg-gray-100 text-gray-600", "Сделка отменена, средства возвращены рекламодателю"),
    ]
    return render(request, "faq.html", {"deal_statuses": deal_statuses})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _redirect_dashboard(user):
    if user.role == User.Role.ADVERTISER:
        return redirect("web:advertiser_dashboard")
    return redirect("web:blogger_dashboard")


# ── Deals ──────────────────────────────────────────────────────────────────────

@login_required
def deal_list(request):
    user = request.user
    if user.role == User.Role.ADVERTISER:
        deals = (
            Deal.objects.filter(advertiser=user)
            .select_related("campaign", "blogger", "platform")
            .order_by("-created_at")
        )
    else:
        deals = (
            Deal.objects.filter(blogger=user)
            .select_related("campaign", "advertiser", "platform")
            .order_by("-created_at")
        )
    return render(request, "deals/list.html", {"deals": deals})


@login_required
def deal_detail(request, pk):
    user = request.user
    if user.role == User.Role.ADVERTISER:
        deal = get_object_or_404(Deal, pk=pk, advertiser=user)
    else:
        deal = get_object_or_404(Deal, pk=pk, blogger=user)

    logs = deal.status_logs.select_related("changed_by").order_by("created_at")
    return render(request, "deals/detail.html", {"deal": deal, "logs": logs})


@login_required
@require_POST
def deal_submit_publication(request, pk):
    """Blogger submits publication URL → status CHECKING."""
    deal = get_object_or_404(Deal, pk=pk, blogger=request.user)

    if deal.status != Deal.Status.IN_PROGRESS:
        messages.error(request, "Добавить публикацию можно только для сделки «В работе».")
        return redirect("web:deal_detail", pk=pk)

    url = request.POST.get("publication_url", "").strip()
    if not url:
        messages.error(request, "Укажите ссылку на публикацию.")
        return redirect("web:deal_detail", pk=pk)

    from django.db import transaction as db_transaction
    with db_transaction.atomic():
        deal.publication_url = url
        deal.publication_at = timezone.now()
        deal.status = Deal.Status.CHECKING
        deal.save(update_fields=["publication_url", "publication_at", "status", "updated_at"])
        DealStatusLog.log(
            deal, Deal.Status.CHECKING,
            changed_by=request.user,
            comment=f"Публикация размещена: {url}",
        )

    messages.success(request, "Ссылка добавлена. Ожидайте подтверждения рекламодателя.")
    return redirect("web:deal_detail", pk=pk)


@login_required
@require_POST
def deal_confirm(request, pk):
    """Advertiser confirms publication → COMPLETED + payment."""
    deal = get_object_or_404(Deal, pk=pk, advertiser=request.user)

    if deal.status != Deal.Status.CHECKING:
        messages.error(request, "Подтвердить можно только сделку «На проверке».")
        return redirect("web:deal_detail", pk=pk)

    from django.db import transaction as db_transaction
    with db_transaction.atomic():
        BillingService.complete_deal_payment(deal)
        deal.status = Deal.Status.COMPLETED
        deal.save(update_fields=["status", "updated_at"])
        DealStatusLog.log(
            deal, Deal.Status.COMPLETED,
            changed_by=request.user,
            comment="Рекламодатель подтвердил публикацию. Оплата выполнена.",
        )

    messages.success(request, f"Сделка завершена. Блогер получил оплату.")
    return redirect("web:deal_detail", pk=pk)


@login_required
@require_POST
def deal_cancel(request, pk):
    """Cancel deal → CANCELLED + release funds."""
    user = request.user
    if user.role == User.Role.ADVERTISER:
        deal = get_object_or_404(Deal, pk=pk, advertiser=user)
    else:
        deal = get_object_or_404(Deal, pk=pk, blogger=user)

    cancellable = {Deal.Status.WAITING_PAYMENT, Deal.Status.IN_PROGRESS}
    if deal.status not in cancellable:
        messages.error(request, "Эту сделку нельзя отменить.")
        return redirect("web:deal_detail", pk=pk)

    from django.db import transaction as db_transaction
    with db_transaction.atomic():
        BillingService.release_funds(deal)
        deal.status = Deal.Status.CANCELLED
        deal.save(update_fields=["status", "updated_at"])
        DealStatusLog.log(
            deal, Deal.Status.CANCELLED,
            changed_by=user,
            comment=f"Отменено пользователем ({user.email}).",
        )

    messages.success(request, "Сделка отменена. Средства возвращены рекламодателю.")
    return redirect("web:deal_list")


# ── Billing / Wallet ───────────────────────────────────────────────────────────

@login_required
def wallet_view(request):
    user = request.user
    wallet, _ = Wallet.objects.get_or_create(user=user)
    transactions = wallet.transactions.order_by("-created_at")[:50]

    withdrawal_submitted = False
    min_withdrawal = getattr(settings, "CURRENCY_MIN_WITHDRAWAL", 500)

    if request.method == "POST" and user.role == User.Role.BLOGGER:
        amount_str = request.POST.get("amount", "").strip()
        card = request.POST.get("card", "").strip()
        try:
            from decimal import Decimal as D
            amount = D(amount_str)
            if amount < D(str(min_withdrawal)):
                messages.error(request, f"Минимальная сумма вывода: {min_withdrawal:,} {getattr(settings, 'CURRENCY_SYMBOL', '')}.")
            elif amount > wallet.available_balance:
                messages.error(request, "Недостаточно средств.")
            elif not card:
                messages.error(request, "Укажите реквизиты для выплаты.")
            else:
                from django.db import transaction as db_transaction
                with db_transaction.atomic():
                    wr = WithdrawalRequest.objects.create(
                        blogger=user,
                        amount=amount,
                        requisites={"type": "card", "details": card},
                    )
                    BillingService.process_withdrawal(wr)
                withdrawal_submitted = True
                messages.success(request, f"Заявка на вывод {amount:,.0f} {getattr(settings, 'CURRENCY_SYMBOL', '')} подана.")
                return redirect("web:wallet")
        except Exception:
            messages.error(request, "Некорректная сумма.")

    pending_withdrawals = []
    if user.role == User.Role.BLOGGER:
        pending_withdrawals = WithdrawalRequest.objects.filter(
            blogger=user, status=WithdrawalRequest.Status.PENDING
        ).order_by("-created_at")

    return render(request, "billing/wallet.html", {
        "wallet": wallet,
        "transactions": transactions,
        "withdrawal_submitted": withdrawal_submitted,
        "pending_withdrawals": pending_withdrawals,
        "min_withdrawal": min_withdrawal,
    })
