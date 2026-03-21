from django.conf import settings
from django.contrib import messages
from django.utils import timezone
from django.contrib.auth import login, logout
import functools

from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.billing.models import Transaction, Wallet, WithdrawalRequest
from apps.billing.services import BillingService
from apps.campaigns.models import Campaign
from apps.campaigns.models import Response as CampaignResponse
from apps.deals.models import Deal, DealStatusLog
from apps.platforms.models import Platform
from apps.profiles.models import AdvertiserProfile, BloggerProfile
from apps.users.models import PasswordResetToken, User
from apps.users.tasks import send_password_reset_email

from .forms import (
    AdvertiserProfileForm,
    BloggerProfileForm,
    CampaignForm,
    LoginForm,
    PasswordResetConfirmForm,
    PasswordResetRequestForm,
    PlatformForm,
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
    """Public blogger profile — visible to authenticated users only."""
    blogger = get_object_or_404(User, pk=pk, role=User.Role.BLOGGER)
    profile, _ = BloggerProfile.objects.get_or_create(user=blogger)
    platforms = Platform.objects.filter(
        blogger=blogger, status=Platform.Status.APPROVED
    ).prefetch_related("categories")
    completed_deals = Deal.objects.filter(
        blogger=blogger, status=Deal.Status.COMPLETED
    ).count()
    return render(request, "profiles/blogger_public.html", {
        "blogger": blogger,
        "profile": profile,
        "platforms": platforms,
        "completed_deals": completed_deals,
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
            {"title": "Разместите и получите оплату", "desc": "Деньги заморожены с момента старта. Разместите рекламу, прикрепите ссылку — оплата гарантирована"},
        ],
        "advertiser_features": [
            {"icon": "🔒", "title": "Эскроу-защита", "desc": "Деньги списываются только после того, как вы лично подтвердили публикацию. Никаких авансов и предоплат."},
            {"icon": "👤", "title": "Профили с реальными метриками", "desc": "Перед принятием отклика — открываете профиль: подписчики, просмотры, ER%, тематики, прайс и история завершённых сделок."},
            {"icon": "⚖️", "title": "Разбор споров", "desc": "Если публикация не соответствует ТЗ — откройте спор. Администратор рассмотрит и вынесет решение."},
            {"icon": "📋", "title": "Контроль бюджета", "desc": "Все сделки и статусы в реальном времени. Неизрасходованный резерв возвращается при отмене."},
        ],
        "blogger_features": [
            {"icon": "✅", "title": "100% гарантия оплаты", "desc": "Деньги рекламодателя заморожены ещё до старта. Даже если он исчезнет — вы получите оплату."},
            {"icon": "⏱", "title": "Авто-защита 72 часа", "desc": "Разместили публикацию — идёт отсчёт. Рекламодатель не ответил за 72ч — деньги зачисляются автоматически."},
            {"icon": "📊", "title": "Профиль как витрина", "desc": "Ваши площадки, метрики и прайс видны рекламодателю в один клик. Заполненный профиль работает как постоянное портфолио."},
            {"icon": "📱", "title": "Несколько площадок", "desc": "Добавляйте любое количество аккаунтов: ВКонтакте, Telegram, YouTube, Instagram, TikTok, Яндекс.Дзен."},
        ],
        "faq_items": [
            {"q": "Сколько стоит использование платформы?", "a": "Регистрация бесплатна. Платформа берёт 15% комиссию только с завершённых сделок — вычитается из выплаты блогеру. Рекламодатель платит ровно столько, сколько указал в кампании."},
            {"q": "Как рекламодатель выбирает блогера?", "a": "При получении отклика — открывает профиль блогера: площадки с подписчиками, просмотрами, ER%, тематики, прайс и история сделок. Принимает решение на основе реальных данных."},
            {"q": "Что если рекламодатель не отвечает после публикации?", "a": "Если в течение 72 часов рекламодатель не подтвердил и не оспорил публикацию — сделка завершается автоматически и деньги поступают блогеру."},
            {"q": "Что делать если возник спор?", "a": "Рекламодатель открывает спор вместо подтверждения. Администратор рассматривает ситуацию и выносит решение: перевести деньги блогеру или вернуть рекламодателю."},
            {"q": "Как вывести заработанные деньги?", "a": f"В разделе «Кошелёк» подайте заявку на вывод (от {getattr(settings, 'CURRENCY_MIN_WITHDRAWAL', 500):,} {getattr(settings, 'CURRENCY_SYMBOL', '₽')}). Укажите реквизиты — обработка в течение 3 рабочих дней."},
            {"q": "Какие соцсети поддерживаются?", "a": "ВКонтакте, Telegram, YouTube, Instagram, TikTok и Яндекс.Дзен. Один блогер может добавить несколько площадок в разных соцсетях."},
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
    user = request.user
    if user.is_staff:
        deal = get_object_or_404(Deal, pk=pk)
    elif user.role == User.Role.ADVERTISER:
        deal = get_object_or_404(Deal, pk=pk, advertiser=user)
    else:
        deal = get_object_or_404(Deal, pk=pk, blogger=user)

    logs = deal.status_logs.select_related("changed_by").order_by("created_at")
    return render(request, "deals/detail.html", {"deal": deal, "logs": logs})


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
    context = {
        "campaigns_moderation": Campaign.objects.filter(status=Campaign.Status.MODERATION).count(),
        "platforms_pending": Platform.objects.filter(status=Platform.Status.PENDING).count(),
        "deals_disputed": Deal.objects.filter(status=Deal.Status.DISPUTED).count(),
        "withdrawals_pending": WithdrawalRequest.objects.filter(status=WithdrawalRequest.Status.PENDING).count(),
        "users_total": User.objects.count(),
        "users_active": User.objects.filter(status=User.Status.ACTIVE).count(),
        "deals_total": Deal.objects.count(),
        "deals_completed": Deal.objects.filter(status=Deal.Status.COMPLETED).count(),
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
    users = (
        User.objects.all()
        .order_by("-date_joined")
    )
    return render(request, "admin_panel/users.html", {"users": users})


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
    messages.success(request, f"Заявка отклонена, средства возвращены на баланс {wr.blogger.email}.")
    return redirect("web:admin_withdrawals")
