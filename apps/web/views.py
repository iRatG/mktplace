from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.db.models import Avg, Count, Sum
from django.utils import timezone
from django.contrib.auth import login, logout
import functools

from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.billing.models import Transaction, Wallet, WithdrawalRequest
from apps.billing.services import BillingService
from apps.campaigns.models import Campaign, DirectOffer
from apps.campaigns.models import Response as CampaignResponse
from apps.deals.models import ChatMessage, Deal, DealStatusLog, Review
from apps.notifications.service import NotificationService
from apps.platforms.models import Category, Platform
from apps.profiles.models import AdvertiserProfile, BloggerProfile
from apps.users.models import PasswordResetToken, User
from apps.users.tasks import send_password_reset_email

from .forms import (
    AdvertiserProfileForm,
    BloggerProfileForm,
    CampaignForm,
    CatalogFilterForm,
    CategoryForm,
    ChatMessageForm,
    CreativeSubmitForm,
    DirectOfferForm,
    LoginForm,
    PasswordResetConfirmForm,
    PasswordResetRequestForm,
    PlatformForm,
    RegisterForm,
    ReviewForm,
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
    if user.is_staff:
        return redirect("web:admin_dashboard")
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
    if user.is_staff:
        return redirect("web:admin_dashboard")
    wallet = getattr(user, "wallet", None)
    active_deals_qs = Deal.objects.filter(blogger=user).exclude(
        status__in=[Deal.Status.COMPLETED, Deal.Status.CANCELLED]
    )
    active_deals = active_deals_qs.select_related("campaign", "platform")[:10]
    profile, _ = BloggerProfile.objects.get_or_create(user=user)

    incoming_offers = (
        DirectOffer.objects.filter(blogger=user, status=DirectOffer.Status.PENDING)
        .select_related("advertiser", "campaign", "platform")
        .order_by("-created_at")
    )

    context = {
        "wallet": wallet,
        "my_responses_count": CampaignResponse.objects.filter(blogger=user).count(),
        "active_deals_count": active_deals_qs.count(),
        "completed_deals_count": Deal.objects.filter(
            blogger=user, status=Deal.Status.COMPLETED
        ).count(),
        "active_deals": active_deals,
        "has_platforms": Platform.objects.filter(blogger=user).exists(),
        "profile_complete": profile.is_complete,
        "incoming_offers": incoming_offers,
    }
    return render(request, "dashboard/blogger.html", context)


# ── Campaigns ─────────────────────────────────────────────────────────────────

@login_required
def campaign_list(request):
    user = request.user
    if user.is_staff:
        campaigns = Campaign.objects.all().select_related("category").order_by("-created_at")
    elif user.role == User.Role.ADVERTISER:
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
        return _redirect_dashboard(request.user)

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

    platform = get_object_or_404(Platform, pk=platform_id, blogger=request.user, status=Platform.Status.APPROVED)

    CampaignResponse.objects.create(
        blogger=request.user,
        campaign=campaign,
        platform=platform,
        content_type=content_type,
        proposed_price=proposed_price,
        message=message,
    )
    NotificationService.notify_new_response(campaign.advertiser, campaign, request.user)
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
            # Re-check limit inside atomic with lock to prevent race condition
            locked_campaign = Campaign.objects.select_for_update().get(pk=campaign.pk)
            if locked_campaign.max_bloggers > 0:
                active_count = Deal.objects.filter(
                    campaign=locked_campaign,
                    status__in=[
                        Deal.Status.IN_PROGRESS,
                        Deal.Status.CHECKING,
                        Deal.Status.ON_APPROVAL,
                        Deal.Status.WAITING_PUBLICATION,
                        Deal.Status.COMPLETED,
                    ],
                ).count()
                if active_count >= locked_campaign.max_bloggers:
                    messages.error(request, f"Достигнут лимит блогеров для кампании ({locked_campaign.max_bloggers}).")
                    return redirect("web:campaign_detail", pk=campaign.pk)

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
            DealStatusLog.log(deal, Deal.Status.IN_PROGRESS, changed_by=request.user, comment="Accepted via web.")
            deal.status = Deal.Status.IN_PROGRESS
            deal.save(update_fields=["status"])
        NotificationService.notify_response_accepted(resp.blogger, campaign, deal)
        messages.success(request, f"Отклик принят. Сделка #{deal.pk} создана.")
    except ValueError as e:
        deal = None
        messages.error(request, f"Недостаточно средств: {e}")

    return redirect("web:campaign_detail", pk=campaign.pk)


@login_required
@require_POST
def response_reject(request, pk):
    resp = get_object_or_404(CampaignResponse, pk=pk, campaign__advertiser=request.user)
    if resp.status == CampaignResponse.Status.PENDING:
        resp.status = CampaignResponse.Status.REJECTED
        resp.save(update_fields=["status"])
        NotificationService.notify_response_rejected(resp.blogger, resp.campaign)
        messages.success(request, "Отклик отклонён.")
    return redirect("web:campaign_detail", pk=resp.campaign_id)


@login_required
def platform_add(request):
    if request.user.role != User.Role.BLOGGER:
        return _redirect_dashboard(request.user)
    form = PlatformForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        platform = form.save(commit=False)
        platform.blogger = request.user
        platform.save()
        form.save_m2m()
        messages.success(request, "Площадка добавлена и отправлена на модерацию.")
        return redirect("web:profile")
    return render(request, "platforms/platform_form.html", {"form": form, "editing": False})


@login_required
def platform_edit(request, pk):
    platform = get_object_or_404(Platform, pk=pk, blogger=request.user)
    form = PlatformForm(request.POST or None, instance=platform)
    if request.method == "POST" and form.is_valid():
        updated = form.save(commit=False)
        # If URL changed on an approved platform — send back to moderation
        url_changed = "url" in form.changed_data
        if url_changed and platform.status == Platform.Status.APPROVED:
            updated.status = Platform.Status.PENDING
            updated.rejection_reason = ""
            messages.warning(request, "URL изменён — площадка отправлена на повторную модерацию.")
        else:
            messages.success(request, "Площадка обновлена.")
        updated.save()
        form.save_m2m()
        return redirect("web:profile")
    return render(request, "platforms/platform_form.html", {"form": form, "editing": True, "platform": platform})


@login_required
@require_POST
def platform_delete(request, pk):
    platform = get_object_or_404(Platform, pk=pk, blogger=request.user)
    if platform.status in (Platform.Status.PENDING, Platform.Status.REJECTED):
        platform.delete()
        messages.success(request, "Площадка удалена.")
    else:
        messages.error(request, "Нельзя удалить одобренную площадку.")
    return redirect("web:profile")


# ── Profiles ──────────────────────────────────────────────────────────────────

@login_required
def profile_view(request):
    user = request.user
    if user.is_staff:
        return redirect("web:admin_dashboard")
    if user.role == User.Role.BLOGGER:
        profile, _ = BloggerProfile.objects.get_or_create(user=user)
        platforms = Platform.objects.filter(blogger=user).prefetch_related("categories")
        completed_deals = Deal.objects.filter(blogger=user, status=Deal.Status.COMPLETED).count()
        return render(request, "profiles/my_profile.html", {
            "profile": profile,
            "platforms": platforms,
            "completed_deals": completed_deals,
        })
    else:
        profile, _ = AdvertiserProfile.objects.get_or_create(user=user)
        return render(request, "profiles/my_profile.html", {
            "profile": profile,
        })


@login_required
def profile_edit(request):
    user = request.user
    if user.is_staff:
        return redirect("web:admin_dashboard")
    if user.role == User.Role.BLOGGER:
        profile, _ = BloggerProfile.objects.get_or_create(user=user)
        form = BloggerProfileForm(request.POST or None, instance=profile)
    else:
        profile, _ = AdvertiserProfile.objects.get_or_create(user=user)
        form = AdvertiserProfileForm(request.POST or None, instance=profile)

    if request.method == "POST" and form.is_valid():
        saved = form.save()
        saved.check_completeness()
        messages.success(request, "Профиль обновлён.")
        return redirect("web:profile")

    return render(request, "profiles/edit_profile.html", {"form": form})


@login_required
def blogger_public_profile(request, pk):
    """Публичный профиль блогера — виден авторизованным пользователям (Модули 3, 7, 10).

    Включает: площадки, метрики, число завершённых сделок,
    последние 10 отзывов с рейтингом (Модуль 7).

    Контекст шаблона:
        blogger         — User (role=BLOGGER)
        profile         — BloggerProfile
        platforms       — одобренные площадки с категориями
        completed_deals — число завершённых сделок
        reviews         — последние 10 отзывов (Review QuerySet)
    """
    blogger = get_object_or_404(User, pk=pk, role=User.Role.BLOGGER)
    profile, _ = BloggerProfile.objects.get_or_create(user=blogger)
    platforms = Platform.objects.filter(
        blogger=blogger, status=Platform.Status.APPROVED
    ).prefetch_related("categories")
    completed_deals = Deal.objects.filter(
        blogger=blogger, status=Deal.Status.COMPLETED
    ).count()
    reviews = Review.objects.filter(target=blogger).select_related("author")[:10]
    return render(request, "profiles/blogger_public.html", {
        "blogger": blogger,
        "profile": profile,
        "platforms": platforms,
        "completed_deals": completed_deals,
        "reviews": reviews,
    })


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
            {"title": "Создайте кампанию", "desc": "Опишите продукт, требования к контенту, форматы и бюджет — черновик можно редактировать до готовности"},
            {"title": "Пройдите модерацию", "desc": "Администраторы проверят кампанию за 24ч и опубликуют в ленте — блогеры начнут откликаться"},
            {"title": "Выберите блогера по профилю", "desc": "Откройте профиль блогера — метрики площадок, тематики, аудитория, прайс и история сделок. Принимайте только тех, кто подходит"},
            {"title": "Подтвердите публикацию", "desc": "Блогер пришлёт ссылку. Нажмите «Подтвердить» — деньги поступят блогеру. Не согласны — откройте спор"},
        ],
        "blogger_steps": [
            {"title": "Заполните профиль", "desc": "Никнейм, описание аудитории, ниша — рекламодатель видит это при проверке вашего отклика. Сильный профиль = больше принятых откликов"},
            {"title": "Добавьте площадку", "desc": "Укажите ссылку, метрики и прайс для ВКонтакте, Telegram, YouTube, Instagram, TikTok или Яндекс.Дзен"},
            {"title": "Пройдите верификацию", "desc": "Администраторы проверят площадку за 24–48ч — один раз и навсегда. Одобренная площадка участвует в сделках"},
            {"title": "Откликнитесь на кампанию", "desc": "Найдите подходящую кампанию, предложите свою цену, добавьте сообщение рекламодателю"},
            {"title": "Согласуйте или публикуйте", "desc": "Опционально: отправьте черновик на согласование — рекламодатель одобрит до публикации. Или сразу прикрепите ссылку на готовый пост"},
            {"title": "Получите оплату", "desc": "Деньги заморожены с момента старта. Рекламодатель подтвердит публикацию или через 72ч — автозачисление на ваш баланс"},
        ],
        "advertiser_features": [
            {"icon": "🔒", "title": "Эскроу-защита", "desc": "Деньги списываются только после того, как вы лично подтвердили публикацию. Никаких авансов и предоплат."},
            {"icon": "👤", "title": "Профили с реальными метриками", "desc": "Перед принятием отклика — открываете профиль: подписчики, просмотры, ER%, тематики, прайс и история завершённых сделок."},
            {"icon": "⚖️", "title": "Разбор споров", "desc": "Если публикация не соответствует ТЗ — откройте спор. Администратор рассмотрит и вынесет решение."},
            {"icon": "📋", "title": "Контроль бюджета", "desc": "Все сделки и статусы в реальном времени. Неизрасходованный резерв возвращается при отмене."},
            {"icon": "📈", "title": "Аналитика расходов", "desc": "Раздел «Аналитика»: общий расход, средняя сумма сделки, конверсия кампаний и разбивка по статусам — всё в одном дашборде."},
        ],
        "blogger_features": [
            {"icon": "✅", "title": "100% гарантия оплаты", "desc": "Деньги рекламодателя заморожены ещё до старта. Даже если он исчезнет — вы получите оплату."},
            {"icon": "⏱", "title": "Авто-защита 72 часа", "desc": "Разместили публикацию — идёт отсчёт. Рекламодатель не ответил за 72ч — деньги зачисляются автоматически."},
            {"icon": "📊", "title": "Профиль как витрина", "desc": "Ваши площадки, метрики и прайс видны рекламодателю в один клик. Заполненный профиль работает как постоянное портфолио."},
            {"icon": "⭐", "title": "Рейтинг и отзывы", "desc": "После каждой завершённой сделки рекламодатель оставляет оценку. Высокий рейтинг — больше выгодных предложений и прямых офферов."},
            {"icon": "📱", "title": "Несколько площадок", "desc": "Добавляйте любое количество аккаунтов: ВКонтакте, Telegram, YouTube, Instagram, TikTok, Яндекс.Дзен."},
            {"icon": "📈", "title": "Аналитика заработка", "desc": "Раздел «Аналитика»: весь заработок, средний доход на сделку, конверсия откликов, рейтинг — наглядно и в одном месте."},
        ],
        "faq_items": [
            {"q": "Сколько стоит использование платформы?", "a": "Регистрация бесплатна. Платформа берёт 15% комиссию только с завершённых сделок — вычитается из выплаты блогеру. Рекламодатель платит ровно столько, сколько указал в кампании."},
            {"q": "Как рекламодатель выбирает блогера?", "a": "При получении отклика — открывает профиль блогера: площадки с подписчиками, просмотрами, ER%, тематики, прайс, рейтинг и история сделок. Принимает решение на основе реальных данных."},
            {"q": "Что если рекламодатель не отвечает после публикации?", "a": "Если в течение 72 часов рекламодатель не подтвердил и не оспорил публикацию — сделка завершается автоматически и деньги поступают блогеру."},
            {"q": "Что такое CPA-кампания?", "a": "Оплата за результат: клик, лид, продажу или установку. Блогер получает уникальную трекинговую ссылку и делится ею с аудиторией. Каждая конверсия = начисление. Работает через постбек или авто-зачисление при клике."},
            {"q": "Как рекламодатель согласует контент до публикации?", "a": "Блогер может загрузить черновик (текст или файл) прямо в сделке. Рекламодатель одобряет или отклоняет с причиной. После одобрения — блогер публикует согласованный вариант. Шаг необязателен."},
            {"q": "Как вывести заработанные деньги?", "a": f"В разделе «Кошелёк» подайте заявку на вывод (от {getattr(settings, 'CURRENCY_MIN_WITHDRAWAL', 500):,} {getattr(settings, 'CURRENCY_SYMBOL', 'UZS')}). Укажите реквизиты — обработка в течение 3 рабочих дней."},
        ],
    }
    return render(request, "landing.html", context)


def faq(request):
    deal_statuses = [
        ("Ожидает оплаты", "bg-yellow-500/15 text-yellow-400", "Сделка создана, средства резервируются на счёте рекламодателя"),
        ("В работе", "bg-blue-500/15 text-blue-400", "Деньги заморожены, блогер приступил к созданию контента"),
        ("На согласовании", "bg-purple-500/15 text-purple-400", "Блогер загрузил черновик, ждёт одобрения рекламодателя"),
        ("Ожидает публикации", "bg-indigo-500/15 text-indigo-400", "Креатив одобрен, блогер публикует контент на площадке"),
        ("На проверке", "bg-orange-500/15 text-orange-400", "Блогер прикрепил ссылку, рекламодатель проверяет (72ч на ответ)"),
        ("Завершена", "bg-green-500/15 text-green-400", "Рекламодатель подтвердил — деньги переведены блогеру (за вычетом 15% комиссии)"),
        ("Оспорена", "bg-red-500/15 text-red-400", "Открыт спор, администратор рассматривает ситуацию и выносит решение"),
        ("Отменена", "bg-slate-500/15 text-slate-400", "Сделка отменена, зарезервированные средства возвращены рекламодателю"),
    ]
    return render(request, "faq.html", {"deal_statuses": deal_statuses})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _redirect_dashboard(user):
    if user.is_staff:
        return redirect("web:admin_dashboard")
    if user.role == User.Role.ADVERTISER:
        return redirect("web:advertiser_dashboard")
    return redirect("web:blogger_dashboard")


# ── Deals ──────────────────────────────────────────────────────────────────────

@login_required
def deal_list(request):
    user = request.user
    if user.is_staff:
        deals = (
            Deal.objects.all()
            .select_related("campaign", "blogger", "advertiser", "platform")
            .order_by("-created_at")
        )
    elif user.role == User.Role.ADVERTISER:
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
    """Детальная страница сделки (Модуль 7).

    Доступ: рекламодатель или блогер участника сделки, либо staff.
    Контекст шаблона:
        deal            — объект Deal
        logs            — история статусов (DealStatusLog)
        can_review      — bool: рекламодатель может оставить отзыв
        existing_review — объект Review (если уже оставлен), иначе None
        review_form     — ReviewForm (только если can_review=True)
    """
    from datetime import timedelta
    user = request.user
    if user.is_staff:
        deal = get_object_or_404(Deal, pk=pk)
    elif user.role == User.Role.ADVERTISER:
        deal = get_object_or_404(Deal, pk=pk, advertiser=user)
    else:
        deal = get_object_or_404(Deal, pk=pk, blogger=user)

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
        deal.save(update_fields=["status", "updated_at"])

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


# ── Catalog / Direct Offers (Module 10) ──────────────────────────────────────

@login_required
def blogger_catalog(request):
    """Каталог одобренных площадок блогеров (Модуль 10).

    Доступ: только рекламодатели (role=ADVERTISER) и is_staff.
    Блогеры и анонимные пользователи перенаправляются на свой дашборд / login.

    GET-параметры (CatalogFilterForm):
        social_type     — тип соцсети (instagram, telegram, youtube, …)
        category        — pk категории (Category)
        min_subscribers — минимальное число подписчиков
        max_subscribers — максимальное число подписчиков
        min_price       — цена за пост от (price_post__gte)
        max_price       — цена за пост до (price_post__lte)
        min_er          — ER% от
        max_er          — ER% до
        min_rating      — минимальный рейтинг блогера (BloggerProfile.rating)
        sort            — сортировка; по умолчанию: по рейтингу убывание

    Контекст шаблона:
        platforms — QuerySet[Platform] (только APPROVED)
        form      — CatalogFilterForm
        total     — int, количество найденных площадок
    """
    if not request.user.is_staff and request.user.role != User.Role.ADVERTISER:
        messages.error(request, "Каталог блогеров доступен только рекламодателям.")
        return _redirect_dashboard(request.user)

    form = CatalogFilterForm(request.GET or None)
    qs = (
        Platform.objects.filter(status=Platform.Status.APPROVED)
        .select_related("blogger__blogger_profile")
        .prefetch_related("categories")
    )

    if form.is_valid():
        cd = form.cleaned_data
        if cd.get("social_type"):
            qs = qs.filter(social_type=cd["social_type"])
        if cd.get("category"):
            qs = qs.filter(categories=cd["category"])
        if cd.get("min_subscribers"):
            qs = qs.filter(subscribers__gte=cd["min_subscribers"])
        if cd.get("max_subscribers"):
            qs = qs.filter(subscribers__lte=cd["max_subscribers"])
        if cd.get("min_price"):
            qs = qs.filter(price_post__gte=cd["min_price"])
        if cd.get("max_price"):
            qs = qs.filter(price_post__lte=cd["max_price"])
        if cd.get("min_er"):
            qs = qs.filter(engagement_rate__gte=cd["min_er"])
        if cd.get("max_er"):
            qs = qs.filter(engagement_rate__lte=cd["max_er"])
        if cd.get("min_rating"):
            qs = qs.filter(blogger__blogger_profile__rating__gte=cd["min_rating"])
        sort = cd.get("sort")
        if sort:
            qs = qs.order_by(sort)
        else:
            qs = qs.order_by("-blogger__blogger_profile__rating")
    else:
        qs = qs.order_by("-blogger__blogger_profile__rating")

    return render(request, "catalog/index.html", {
        "platforms": qs,
        "form": form,
        "total": qs.count(),
    })


@login_required
def direct_offer_create(request, platform_pk):
    """Создание прямого предложения от рекламодателя блогеру (Модуль 10).

    Доступ: только role=ADVERTISER. Площадка должна быть APPROVED (иначе 404).

    GET  — отображает форму DirectOfferForm; если уже есть PENDING-оффер для
           этой площадки — показывает предупреждение вместо формы.
    POST — создаёт DirectOffer со статусом PENDING.
           Гарды:
             • нельзя создать второй оффер для той же (advertiser, campaign, platform),
               если предыдущий не REJECTED;
             • кампания должна принадлежать текущему рекламодателю и быть ACTIVE.
           При успехе: redirect → blogger_catalog с flash-сообщением.

    URL-параметры:
        platform_pk — pk площадки (Platform)

    Контекст шаблона:
        platform       — Platform
        blogger        — User (владелец площадки)
        form           — DirectOfferForm
        existing_offer — DirectOffer|None (PENDING оффер, если есть)
    """
    if request.user.role != User.Role.ADVERTISER:
        messages.error(request, "Только рекламодатели могут отправлять предложения.")
        return _redirect_dashboard(request.user)

    platform = get_object_or_404(Platform, pk=platform_pk, status=Platform.Status.APPROVED)
    blogger = platform.blogger

    # Check for existing pending offer
    existing = DirectOffer.objects.filter(
        advertiser=request.user,
        campaign__in=Campaign.objects.filter(advertiser=request.user),
        platform=platform,
        status=DirectOffer.Status.PENDING,
    ).first()

    form = DirectOfferForm(advertiser=request.user, data=request.POST or None)

    if request.method == "POST" and form.is_valid():
        campaign = form.cleaned_data["campaign"]

        # Guard: no duplicate offer for same advertiser+campaign+platform
        if DirectOffer.objects.filter(
            advertiser=request.user, campaign=campaign, platform=platform
        ).exclude(status=DirectOffer.Status.REJECTED).exists():
            messages.error(request, "Предложение для этой площадки в рамках данной кампании уже отправлено.")
            return redirect("web:blogger_public_profile", pk=blogger.pk)

        DirectOffer.objects.create(
            advertiser=request.user,
            blogger=blogger,
            campaign=campaign,
            platform=platform,
            content_type=form.cleaned_data["content_type"],
            proposed_price=form.cleaned_data.get("proposed_price"),
            message=form.cleaned_data.get("message", ""),
        )
        NotificationService.notify_direct_offer_received(blogger, campaign, request.user)
        messages.success(request, f"Предложение отправлено блогеру {blogger.email}!")
        return redirect("web:blogger_catalog")

    return render(request, "catalog/direct_offer.html", {
        "platform": platform,
        "blogger": blogger,
        "form": form,
        "existing_offer": existing,
    })


@login_required
@require_POST
def direct_offer_accept(request, pk):
    """Блогер принимает прямое предложение — создаётся Сделка (Модуль 10).

    Доступ: только владелец оффера (blogger=request.user), статус PENDING.
    Рекламодатель и чужие блогеры получают 404.

    Логика (всё внутри atomic + select_for_update):
        1. Проверка что кампания ACTIVE.
        2. Определение суммы сделки: proposed_price ?? campaign.fixed_price.
        3. Проверка лимита участников кампании (max_bloggers).
        4. Повторная блокировка оффера (select_for_update) — гард от двойного принятия.
        5. Deal.objects.create(status=WAITING_PAYMENT).
        6. BillingService.reserve_funds(deal) — резервирует средства у рекламодателя.
           При ValueError (нет средств) транзакция откатывается, deal=None.
        7. DealStatusLog.log → статус → IN_PROGRESS.
        8. DirectOffer.status → ACCEPTED, DirectOffer.deal → созданная сделка.

    При успехе: redirect → deal_detail.
    При ошибке резервирования: redirect → blogger_dashboard с сообщением об ошибке.

    URL-параметры:
        pk — pk DirectOffer
    """
    offer = get_object_or_404(
        DirectOffer, pk=pk, blogger=request.user, status=DirectOffer.Status.PENDING
    )

    campaign = offer.campaign
    if campaign.status != Campaign.Status.ACTIVE:
        messages.error(request, "Кампания больше не активна.")
        return redirect("web:blogger_dashboard")

    amount = offer.proposed_price or campaign.fixed_price
    if not amount:
        messages.error(request, "Не удалось определить сумму сделки.")
        return redirect("web:blogger_dashboard")

    from django.db import transaction as db_transaction
    deal = None
    try:
        with db_transaction.atomic():
            locked_campaign = Campaign.objects.select_for_update().get(pk=campaign.pk)
            if locked_campaign.max_bloggers > 0:
                active_count = Deal.objects.filter(
                    campaign=locked_campaign,
                    status__in=[
                        Deal.Status.IN_PROGRESS, Deal.Status.CHECKING,
                        Deal.Status.ON_APPROVAL, Deal.Status.WAITING_PUBLICATION,
                        Deal.Status.COMPLETED,
                    ],
                ).count()
                if active_count >= locked_campaign.max_bloggers:
                    messages.error(request, "Достигнут лимит участников кампании.")
                    return redirect("web:blogger_dashboard")

            locked_offer = DirectOffer.objects.select_for_update().get(pk=offer.pk)
            if locked_offer.status != DirectOffer.Status.PENDING:
                messages.error(request, "Предложение уже обработано.")
                return redirect("web:blogger_dashboard")

            deal = Deal.objects.create(
                campaign=locked_campaign,
                blogger=request.user,
                platform=offer.platform,
                advertiser=offer.advertiser,
                amount=amount,
                status=Deal.Status.WAITING_PAYMENT,
            )
            BillingService.reserve_funds(deal)
            DealStatusLog.log(deal, Deal.Status.IN_PROGRESS, changed_by=offer.advertiser, comment="Accepted direct offer.")
            deal.status = Deal.Status.IN_PROGRESS
            deal.save(update_fields=["status"])

            locked_offer.status = DirectOffer.Status.ACCEPTED
            locked_offer.deal = deal
            locked_offer.save(update_fields=["status", "deal", "updated_at"])

        NotificationService.notify_direct_offer_accepted(
            offer.advertiser, locked_campaign, request.user, deal
        )
        messages.success(request, f"Предложение принято. Сделка #{deal.pk} создана!")
    except ValueError as e:
        deal = None
        messages.error(request, f"Недостаточно средств у рекламодателя: {e}")

    return redirect("web:deal_detail", pk=deal.pk) if deal else redirect("web:blogger_dashboard")


@login_required
@require_POST
def direct_offer_reject(request, pk):
    """Блогер отклоняет прямое предложение (Модуль 10).

    Доступ: только владелец оффера (blogger=request.user), статус PENDING.
    Рекламодатель и чужие блогеры получают 404.

    Устанавливает DirectOffer.status = REJECTED.
    Финансовых операций не производит (деньги не резервировались).

    При успехе: redirect → blogger_dashboard с flash-сообщением.

    URL-параметры:
        pk — pk DirectOffer
    """
    offer = get_object_or_404(
        DirectOffer, pk=pk, blogger=request.user, status=DirectOffer.Status.PENDING
    )
    offer.status = DirectOffer.Status.REJECTED
    offer.save(update_fields=["status", "updated_at"])
    NotificationService.notify_direct_offer_rejected(offer.advertiser, offer.campaign, request.user)
    messages.success(request, "Предложение отклонено.")
    return redirect("web:blogger_dashboard")


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
        "withdrawal_submitted": withdrawal_submitted,
        "pending_withdrawals": pending_withdrawals,
        "min_withdrawal": min_withdrawal,
    })


# ── Admin (staff only) ────────────────────────────────────────────────────────

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
                              comment=f"Спор решён администратором: оплата блогеру. {comment}")
            BillingService.complete_deal_payment(locked)
            locked.status = Deal.Status.COMPLETED
            msg = "Спор решён — оплата переведена блогеру."
        else:
            DealStatusLog.log(locked, Deal.Status.CANCELLED, changed_by=request.user,
                              comment=f"Спор решён администратором: возврат рекламодателю. {comment}")
            BillingService.release_funds(locked)
            locked.status = Deal.Status.CANCELLED
            msg = "Спор решён — средства возвращены рекламодателю."

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


# ── Notifications (Module 11) ─────────────────────────────────────────────────

@login_required
def notification_list(request):
    """Страница уведомлений пользователя (Модуль 11).

    Показывает последние 50 уведомлений: непрочитанные сверху, затем прочитанные.
    Помечает все уведомления как прочитанные при открытии страницы.

    Доступ: любой авторизованный пользователь.

    Контекст шаблона:
        notifications — QuerySet[Notification] последних 50 записей
        unread_count  — int, число непрочитанных ДО открытия страницы
    """
    from apps.notifications.models import Notification
    qs = Notification.objects.filter(user=request.user).select_related("related_deal")
    unread_count = qs.filter(is_read=False).count()
    notifications_qs = qs[:50]
    # Помечаем все как прочитанные
    qs.filter(is_read=False).update(is_read=True)
    return render(request, "notifications/list.html", {
        "notifications": notifications_qs,
        "unread_count": unread_count,
    })


@login_required
@require_POST
def notification_mark_all_read(request):
    """Отметить все уведомления как прочитанные (Модуль 11).

    POST-only. После выполнения: redirect → notification_list.
    """
    from apps.notifications.models import Notification
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    messages.success(request, "Все уведомления отмечены как прочитанные.")
    return redirect("web:notifications")


# ── Reviews (Module 7) ────────────────────────────────────────────────────────

@login_required
@require_POST
def deal_review_submit(request, pk):
    """Отправить отзыв о сделке — только рекламодатель, только COMPLETED, окно 7 дней (Модуль 7).

    POST /deals/<pk>/review/

    Создаёт Review (author=advertiser, target=blogger). После создания
    пересчитывает BloggerProfile.rating как среднее всех полученных отзывов.

    Редиректы → deal_detail.
    """
    from datetime import timedelta
    from django.db.models import Avg

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


# ── Admin: user management (Module 13) ───────────────────────────────────────

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


# ── Admin: categories CRUD (Module 13) ───────────────────────────────────────

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


# ── Analytics (Module 12) ──────────────────────────────────────────────────────

@login_required
def analytics_view(request):
    """Аналитика: маршрутизирует по роли на соответствующий шаблон."""
    user = request.user
    if user.is_staff:
        return redirect("web:admin_dashboard")

    if user.role == User.Role.ADVERTISER:
        return _analytics_advertiser(request, user)
    return _analytics_blogger(request, user)


def _analytics_advertiser(request, user):
    """Аналитический дашборд для рекламодателя."""
    deals_qs = Deal.objects.filter(advertiser=user)
    total_deals = deals_qs.count()
    completed_deals = deals_qs.filter(status=Deal.Status.COMPLETED).count()
    cancelled_deals = deals_qs.filter(status=Deal.Status.CANCELLED).count()
    active_deals = deals_qs.exclude(
        status__in=[Deal.Status.COMPLETED, Deal.Status.CANCELLED]
    ).count()
    completion_rate = round(completed_deals / total_deals * 100) if total_deals else 0

    avg_deal = (
        deals_qs.filter(status=Deal.Status.COMPLETED)
        .aggregate(avg=Avg("amount"))["avg"]
        or Decimal("0")
    )

    total_spent = (
        Transaction.objects.filter(wallet__user=user, type=Transaction.Type.PAYMENT)
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )
    total_deposited = (
        Transaction.objects.filter(wallet__user=user, type=Transaction.Type.DEPOSIT)
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )

    campaigns_qs = Campaign.objects.filter(advertiser=user)
    campaigns_by_status = {
        "active": campaigns_qs.filter(status=Campaign.Status.ACTIVE).count(),
        "completed": campaigns_qs.filter(status=Campaign.Status.COMPLETED).count(),
        "draft": campaigns_qs.filter(status=Campaign.Status.DRAFT).count(),
        "paused": campaigns_qs.filter(status=Campaign.Status.PAUSED).count(),
    }

    recent_completed = (
        deals_qs.filter(status=Deal.Status.COMPLETED)
        .select_related("blogger", "campaign")
        .order_by("-updated_at")[:5]
    )

    context = {
        "total_deals": total_deals,
        "completed_deals": completed_deals,
        "cancelled_deals": cancelled_deals,
        "active_deals": active_deals,
        "completion_rate": completion_rate,
        "avg_deal": avg_deal,
        "total_spent": total_spent,
        "total_deposited": total_deposited,
        "campaigns_by_status": campaigns_by_status,
        "recent_completed": recent_completed,
    }
    return render(request, "analytics/advertiser.html", context)


def _analytics_blogger(request, user):
    """Аналитический дашборд для блогера."""
    deals_qs = Deal.objects.filter(blogger=user)
    total_deals = deals_qs.count()
    completed_deals = deals_qs.filter(status=Deal.Status.COMPLETED).count()
    cancelled_deals = deals_qs.filter(status=Deal.Status.CANCELLED).count()
    active_deals = deals_qs.exclude(
        status__in=[Deal.Status.COMPLETED, Deal.Status.CANCELLED]
    ).count()
    completion_rate = round(completed_deals / total_deals * 100) if total_deals else 0

    avg_earning = (
        deals_qs.filter(status=Deal.Status.COMPLETED)
        .aggregate(avg=Avg("amount"))["avg"]
        or Decimal("0")
    )
    total_earned = (
        Transaction.objects.filter(wallet__user=user, type=Transaction.Type.EARNING)
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )

    total_responses = CampaignResponse.objects.filter(blogger=user).count()
    accepted_responses = CampaignResponse.objects.filter(
        blogger=user, status=CampaignResponse.Status.ACCEPTED
    ).count()
    acceptance_rate = (
        round(accepted_responses / total_responses * 100) if total_responses else 0
    )

    profile, _ = BloggerProfile.objects.get_or_create(user=user)

    recent_completed = (
        deals_qs.filter(status=Deal.Status.COMPLETED)
        .select_related("campaign")
        .order_by("-updated_at")[:5]
    )

    context = {
        "total_deals": total_deals,
        "completed_deals": completed_deals,
        "cancelled_deals": cancelled_deals,
        "active_deals": active_deals,
        "completion_rate": completion_rate,
        "avg_earning": avg_earning,
        "total_earned": total_earned,
        "total_responses": total_responses,
        "accepted_responses": accepted_responses,
        "acceptance_rate": acceptance_rate,
        "rating": profile.rating,
        "recent_completed": recent_completed,
    }
    return render(request, "analytics/blogger.html", context)


# ── Chat (Sprint 6) ────────────────────────────────────────────────────────────

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


# ── Creative Approval (Sprint 7) ───────────────────────────────────────────────

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


# ── CPA Tracking (Sprint 8) ──────────────────────────────────────────────────

def cpa_click_track(request, slug):
    """Публичный endpoint: /t/<slug>/

    Логирует клик, создаёт конверсию (для click-type сразу начисляет).
    Редиректит на cpa_tracking_url кампании или на '/' если не задан.
    Авторизация не требуется — публичная ссылка для конечных пользователей.
    """
    from apps.deals.models import ClickLog, Conversion, TrackingLink
    from apps.billing.services import BillingService

    try:
        tl = TrackingLink.objects.select_related(
            "deal__campaign"
        ).get(slug=slug, is_active=True)
    except TrackingLink.DoesNotExist:
        from django.http import Http404
        raise Http404

    # Log the click
    ip = (
        request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
        or request.META.get("REMOTE_ADDR")
        or None
    )
    ua = request.META.get("HTTP_USER_AGENT", "")[:1000]
    click = ClickLog.objects.create(tracking_link=tl, ip=ip, user_agent=ua)

    campaign = tl.deal.campaign
    cpa_type = campaign.cpa_type or ""
    cpa_rate = campaign.cpa_rate

    if cpa_type == campaign.CPAType.CLICK and cpa_rate:
        # Immediate conversion + billing
        conversion = Conversion.objects.create(
            tracking_link=tl,
            click_log=click,
            conversion_type=Conversion.ConversionType.CLICK,
            amount=cpa_rate,
        )
        try:
            BillingService.credit_cpa_conversion(conversion)
        except ValueError:
            pass  # insufficient funds — conversion stays uncredited

    # Redirect to target URL, appending click_id for postback attribution
    target_url = campaign.cpa_tracking_url or "/"
    if "?" in target_url:
        target_url += f"&click_id={click.click_id}"
    else:
        target_url += f"?click_id={click.click_id}"

    return redirect(target_url)


def cpa_postback(request):
    """Публичный postback endpoint: /pb/?click_id=UUID&goal=lead

    Принимает GET/POST.
    Обязательный параметр: click_id (UUID)
    Опциональный: goal (lead/sale/install), по умолчанию lead.
    Создаёт Conversion и начисляет через BillingService.
    Идемпотентен: повторный постбек с тем же click_id игнорируется.
    """
    from apps.deals.models import ClickLog, Conversion
    from apps.billing.services import BillingService

    params = request.GET if request.method == "GET" else request.POST
    click_id_raw = params.get("click_id", "").strip()
    goal = params.get("goal", "lead").strip().lower()

    if not click_id_raw:
        from django.http import JsonResponse
        return JsonResponse({"status": "error", "detail": "click_id required"}, status=400)

    try:
        import uuid
        click_id = uuid.UUID(click_id_raw)
    except ValueError:
        from django.http import JsonResponse
        return JsonResponse({"status": "error", "detail": "invalid click_id"}, status=400)

    try:
        click = ClickLog.objects.select_related(
            "tracking_link__deal__campaign"
        ).get(click_id=click_id)
    except ClickLog.DoesNotExist:
        from django.http import JsonResponse
        return JsonResponse({"status": "error", "detail": "click not found"}, status=404)

    # Map goal → ConversionType
    goal_map = {
        "lead": Conversion.ConversionType.LEAD,
        "sale": Conversion.ConversionType.SALE,
        "install": Conversion.ConversionType.INSTALL,
    }
    conv_type = goal_map.get(goal, Conversion.ConversionType.LEAD)

    # Idempotency: one credited conversion per click per goal
    already = Conversion.objects.filter(
        click_log=click, conversion_type=conv_type, credited=True
    ).exists()
    if already:
        from django.http import JsonResponse
        return JsonResponse({"status": "ok", "detail": "already credited"})

    campaign = click.tracking_link.deal.campaign
    cpa_rate = campaign.cpa_rate
    if not cpa_rate:
        from django.http import JsonResponse
        return JsonResponse({"status": "error", "detail": "no cpa_rate on campaign"}, status=400)

    import json as _json
    postback_raw = _json.dumps(dict(params))

    conversion = Conversion.objects.create(
        tracking_link=click.tracking_link,
        click_log=click,
        conversion_type=conv_type,
        amount=cpa_rate,
        postback_raw=postback_raw,
    )
    try:
        BillingService.credit_cpa_conversion(conversion)
        from django.http import JsonResponse
        return JsonResponse({"status": "ok", "conversion_id": conversion.pk})
    except ValueError as e:
        from django.http import JsonResponse
        return JsonResponse({"status": "error", "detail": str(e)}, status=402)
