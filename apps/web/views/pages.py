from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.billing.formatting import format_money
from apps.campaigns.models import Campaign, DirectOffer
from apps.campaigns.models import Response as CampaignResponse
from apps.deals.models import Deal
from apps.platforms.models import Platform
from apps.profiles.models import BloggerProfile
from apps.users.models import User


def _redirect_dashboard(user):
    if user.is_staff:
        return redirect("web:admin_dashboard")
    if user.role == User.Role.ADVERTISER:
        return redirect("web:advertiser_dashboard")
    return redirect("web:blogger_dashboard")


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
            {"title": "Создайте рекламную кампанию", "desc": "Опишите продукт, требования к контенту, форматы и бюджет — черновик можно редактировать до готовности"},
            {"title": "Пройдите модерацию", "desc": "Администраторы проверят рекламную кампанию за 24ч и опубликуют в ленте — блогеры начнут откликаться"},
            {"title": "Выберите блогера по профилю", "desc": "Откройте профиль блогера — метрики площадок, тематики, аудитория, прайс и история сделок. Принимайте только тех, кто подходит"},
            {"title": "Подтвердите публикацию", "desc": "Блогер пришлёт ссылку. Нажмите «Подтвердить» — деньги поступят блогеру. Не согласны — откройте спор"},
        ],
        "blogger_steps": [
            {"title": "Заполните профиль", "desc": "Никнейм, описание аудитории, ниша — рекламодатель видит это при проверке вашего отклика. Сильный профиль = больше принятых откликов"},
            {"title": "Добавьте площадку", "desc": "Укажите ссылку, метрики и прайс для ВКонтакте, Telegram, YouTube, Instagram, TikTok или Яндекс.Дзен"},
            {"title": "Пройдите верификацию", "desc": "Администраторы проверят площадку за 24–48ч — один раз и навсегда. Одобренная площадка участвует в сделках"},
            {"title": "Откликнитесь на рекламную кампанию", "desc": "Найдите подходящую рекламную кампанию, предложите свою цену, добавьте сообщение рекламодателю"},
            {"title": "Согласуйте или публикуйте", "desc": "Опционально: отправьте черновик на согласование — рекламодатель одобрит до публикации. Или сразу прикрепите ссылку на готовый пост"},
            {"title": "Получите оплату", "desc": "Деньги заморожены с момента старта. Нет претензии — по окончании срока претензии и срока сохранения публикации деньги зачисляются автоматически"},
        ],
        "advertiser_features": [
            {"icon": "🔒", "title": "Эскроу-защита", "desc": "Деньги списываются только после того, как вы лично подтвердили публикацию. Никаких авансов и предоплат."},
            {"icon": "👤", "title": "Профили с реальными метриками", "desc": "Перед принятием отклика — открываете профиль: подписчики, просмотры, ER%, тематики, прайс и история завершённых сделок."},
            {"icon": "⚖️", "title": "Разбор споров", "desc": "Если публикация не соответствует ТЗ — откройте спор. Платформа проведёт досудебное рассмотрение и сопроводит урегулирование."},
            {"icon": "📋", "title": "Контроль бюджета", "desc": "Все сделки и статусы в реальном времени. Неизрасходованный резерв возвращается при отмене."},
            {"icon": "📈", "title": "Аналитика расходов", "desc": "Раздел «Аналитика»: общий расход, средняя сумма сделки, конверсия кампаний и разбивка по статусам — всё в одном дашборде."},
        ],
        "blogger_features": [
            {"icon": "✅", "title": "100% гарантия оплаты", "desc": "Деньги рекламодателя заморожены ещё до старта. Даже если он исчезнет — вы получите оплату."},
            {"icon": "⏱", "title": "Оплата по сроку", "desc": "Разместили публикацию — у рекламодателя 3 рабочих дня на претензию. Нет претензии и истёк срок сохранения публикации — деньги зачисляются автоматически."},
            {"icon": "📊", "title": "Профиль как витрина", "desc": "Ваши площадки, метрики и прайс видны рекламодателю в один клик. Заполненный профиль работает как постоянное портфолио."},
            {"icon": "⭐", "title": "Рейтинг и отзывы", "desc": "После каждой завершённой сделки рекламодатель оставляет оценку. Высокий рейтинг — больше выгодных предложений и прямых офферов."},
            {"icon": "📱", "title": "Несколько площадок", "desc": "Добавляйте любое количество аккаунтов: ВКонтакте, Telegram, YouTube, Instagram, TikTok, Яндекс.Дзен."},
            {"icon": "📈", "title": "Аналитика заработка", "desc": "Раздел «Аналитика»: весь заработок, средний доход на сделку, конверсия откликов, рейтинг — наглядно и в одном месте."},
        ],
        "faq_items": [
            {"q": "Сколько стоит использование платформы?", "a": "Регистрация бесплатна. Комиссия платформы — 8–16% по тарифу (зависит от оборота рекламодателя за прошлый месяц), только с завершённых сделок. Комиссию платит рекламодатель сверх цены блогера — блогер получает свою цену целиком."},
            {"q": "Как рекламодатель выбирает блогера?", "a": "При получении отклика — открывает профиль блогера: площадки с подписчиками, просмотрами, ER%, тематики, прайс, рейтинг и история сделок. Принимает решение на основе реальных данных."},
            {"q": "Что если рекламодатель не отвечает после публикации?", "a": "Если за 3 рабочих дня рекламодатель не подал претензию, результат считается принятым. Деньги поступают блогеру автоматически, когда истечёт и минимальный срок сохранения публикации из оферты."},
            {"q": "Что такое CPA-кампания?", "a": "Оплата за результат: клик, лид, продажу или установку. Блогер получает уникальную трекинговую ссылку и делится ею с аудиторией. Каждая конверсия = начисление. Работает через постбек или авто-зачисление при клике."},
            {"q": "Как рекламодатель согласует контент до публикации?", "a": "Блогер может загрузить черновик (текст или файл) прямо в сделке. Рекламодатель одобряет или отклоняет с причиной. После одобрения — блогер публикует согласованный вариант. Шаг необязателен."},
            {"q": "Как вывести заработанные деньги?", "a": f"В разделе «Кошелёк» подайте заявку на вывод (от {format_money(getattr(settings, 'CURRENCY_MIN_WITHDRAWAL', 500))} {getattr(settings, 'CURRENCY_SYMBOL', 'UZS')}). Укажите реквизиты — обработка в течение 3 рабочих дней."},
        ],
    }
    return render(request, "landing.html", context)


def faq(request):
    deal_statuses = [
        ("Ожидает оплаты", "bg-yellow-100 text-yellow-700", "Сделка создана, средства резервируются на счёте рекламодателя"),
        ("В работе", "bg-blue-100 text-blue-600", "Деньги заморожены, блогер приступил к созданию контента"),
        ("На согласовании", "bg-purple-100 text-purple-600", "Блогер загрузил черновик, ждёт одобрения рекламодателя"),
        ("Ждёт публикации", "bg-indigo-100 text-indigo-600", "Креатив одобрен, блогер публикует контент на площадке"),
        ("На проверке", "bg-orange-100 text-orange-600", "Блогер прикрепил ссылку, идёт срок претензии и сохранения публикации"),
        ("Завершена", "bg-green-100 text-green-600", "Оплата проведена — деньги переведены блогеру"),
        ("Оспорена", "bg-red-100 text-red-600", "Подана претензия, деньги депонированы — Платформа рассматривает и принимает решение"),
        ("Отменена", "bg-gray-100 text-gray-600", "Сделка отменена, зарезервированные средства возвращены рекламодателю"),
    ]
    return render(request, "faq.html", {"deal_statuses": deal_statuses})


def support_view(request):
    from ..forms import SupportMessageForm

    initial = {}
    if request.user.is_authenticated:
        initial["email"] = request.user.email

    form = SupportMessageForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        from apps.notifications.tasks import send_support_message_email

        send_support_message_email.delay(
            form.cleaned_data["name"],
            form.cleaned_data["email"],
            form.cleaned_data["message"],
        )
        messages.success(request, "Сообщение отправлено! Мы ответим в течение рабочего дня.")
        return redirect("web:support")

    return render(request, "support.html", {"form": form})


def terms_view(request):
    """Пользовательское соглашение (REQ-6)."""
    return render(request, "legal/terms.html")


def oferta_view(request):
    """Оферта Рекламодатель–Исполнитель (REQ-6)."""
    return render(request, "legal/oferta.html")


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
        # «Требует действия»: где ждут шага рекламодателя (по 10 на тип).
        "action_deals": (
            Deal.objects.filter(advertiser=user, status__in=[Deal.Status.ON_APPROVAL, Deal.Status.CHECKING])
            .select_related("campaign", "blogger").order_by("-updated_at")[:10]
        ),
        "action_campaigns": (
            Campaign.objects.filter(advertiser=user, responses__status=CampaignResponse.Status.PENDING)
            .annotate(pending=Count("responses")).order_by("-pending")[:10]
        ),
    }
    return render(request, "dashboard/advertiser.html", context)


def rejected_responses_to_retry(blogger, limit=10):
    """Отклонённые отклики с комментарием, на которые можно откликнуться снова (QA camp_test_3, шаг 7.1).

    Кампания активна и срок не истёк, у блогера нет по ней ожидающего или принятого отклика; по кампании —
    только последний отклонённый.
    """
    from django.utils import timezone

    busy = CampaignResponse.objects.filter(
        blogger=blogger, status__in=(CampaignResponse.Status.PENDING, CampaignResponse.Status.ACCEPTED),
    ).values("campaign")
    today = timezone.localdate()
    candidates = (
        CampaignResponse.objects.filter(
            blogger=blogger, status=CampaignResponse.Status.REJECTED, campaign__status=Campaign.Status.ACTIVE,
        )
        .exclude(rejection_reason="")
        .exclude(campaign__in=busy)
        .exclude(campaign__end_date__lt=today)
        .select_related("campaign")
        .order_by("-updated_at")
    )
    result, seen = [], set()
    for resp in candidates:
        if resp.campaign_id in seen:
            continue
        seen.add(resp.campaign_id)
        result.append(resp)
        if len(result) == limit:
            break
    return result


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
        DirectOffer.objects.filter(blogger=user, status=DirectOffer.Status.PENDING, expires_at__gt=timezone.now())
        .select_related("advertiser", "advertiser__advertiser_profile", "campaign", "platform")
        .order_by("-created_at")
    )

    from apps.campaigns.models import CampaignInvitation

    invitations = (
        CampaignInvitation.objects.filter(blogger=user, campaign__status=Campaign.Status.ACTIVE)
        .exclude(campaign__responses__blogger=user)
        .select_related("campaign")[:10]
    )

    context = {
        "invitations": invitations,
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
        # «Требует действия»: где ждут шага блогера (по 10 записей).
        "action_deals": (
            Deal.objects.filter(
                blogger=user, status__in=[Deal.Status.IN_PROGRESS, Deal.Status.WAITING_PUBLICATION],
            ).select_related("campaign").order_by("-updated_at")[:10]
        ),
        "rejected_responses": rejected_responses_to_retry(user),
    }
    return render(request, "dashboard/blogger.html", context)
