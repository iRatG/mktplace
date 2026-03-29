from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

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
