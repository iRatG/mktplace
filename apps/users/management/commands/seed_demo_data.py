"""
seed_demo_data — полная эмуляция бизнес-цикла платформы.

Создаёт 4 сценария, покрывающих весь жизненный цикл сделки:

  Сценарий A: Кампания активна, отклик ожидает ответа
  Сценарий B: Отклик принят → сделка IN_PROGRESS (деньги заморожены)
  Сценарий C: Сделка на проверке → CHECKING (блогер "опубликовал")
  Сценарий D: Сделка завершена → COMPLETED (деньги выплачены блогеру)

Запуск:
    python manage.py seed_demo_data           # создаёт данные
    python manage.py seed_demo_data --reset   # удаляет старые и пересоздаёт
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.billing.models import Transaction, Wallet
from apps.billing.services import BillingService
from apps.campaigns.models import Campaign, DirectOffer
from apps.campaigns.models import Response as CampaignResponse
from apps.deals.models import Deal, DealStatusLog
from apps.platforms.models import Category, Platform
from apps.users.models import User


# ─── Константы демо-данных ────────────────────────────────────────────────────

ADVERTISER_EMAIL = "advertiser@demo.com"
BLOGGER_EMAIL = "blogger@demo.com"
BLOGGER2_EMAIL = "blogger2@demo.com"   # Telegram-блогер (каталог)
BLOGGER3_EMAIL = "blogger3@demo.com"   # YouTube-блогер (каталог)

INITIAL_BALANCE = Decimal("2_000_000")  # 2 млн UZS на кошельке рекламодателя
BLOGGER_INITIAL = Decimal("150_000")    # стартовый баланс блогера

CAMPAIGNS = [
    {
        "name": "Реклама фитнес-приложения FitLife",
        "description": (
            "Ищем блогеров для продвижения нашего фитнес-приложения FitLife. "
            "Приложение помогает составлять персональные тренировки и отслеживать прогресс. "
            "Нужны искренние обзоры и личный опыт использования."
        ),
        "payment_type": Campaign.PaymentType.FIXED,
        "fixed_price": Decimal("350_000"),
        "budget": Decimal("3_500_000"),
        "content_types": ["post", "stories"],
        "allowed_socials": ["instagram", "telegram"],
        "min_subscribers": 5000,
        "max_bloggers": 10,
        "status": Campaign.Status.ACTIVE,
    },
    {
        "name": "Запуск онлайн-курса по дизайну",
        "description": (
            "Продвигаем новый онлайн-курс «UX/UI с нуля». "
            "Целевая аудитория — студенты и начинающие дизайнеры. "
            "Приветствуется демонстрация платформы в формате видео."
        ),
        "payment_type": Campaign.PaymentType.FIXED,
        "fixed_price": Decimal("500_000"),
        "budget": Decimal("5_000_000"),
        "content_types": ["video", "review"],
        "allowed_socials": ["youtube", "telegram"],
        "min_subscribers": 10000,
        "max_bloggers": 5,
        "status": Campaign.Status.ACTIVE,
    },
    {
        "name": "Продвижение доставки еды YumBox",
        "description": (
            "YumBox — сервис доставки здоровой еды. "
            "Хотим охватить аудиторию 25-35 лет, интересующуюся ЗОЖ. "
            "Промокод для подписчиков: YUMBOX20 (скидка 20%)."
        ),
        "payment_type": Campaign.PaymentType.FIXED,
        "fixed_price": Decimal("280_000"),
        "budget": Decimal("2_800_000"),
        "content_types": ["post", "stories", "reels"],
        "allowed_socials": ["instagram", "tiktok"],
        "min_subscribers": 3000,
        "max_bloggers": 15,
        "status": Campaign.Status.ACTIVE,
    },
]


class Command(BaseCommand):
    help = "Заполняет базу демо-данными: полный бизнес-цикл платформы"

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Удалить старые демо-данные перед созданием новых",
        )

    def handle(self, *args, **options):
        if options["reset"]:
            self._cleanup()

        self.stdout.write("\n📦 Создание демо-данных...\n")

        advertiser = self._get_user(ADVERTISER_EMAIL, User.Role.ADVERTISER)
        blogger = self._get_user(BLOGGER_EMAIL, User.Role.BLOGGER)
        blogger2 = self._get_or_create_extra_blogger(BLOGGER2_EMAIL, "blogger2")
        blogger3 = self._get_or_create_extra_blogger(BLOGGER3_EMAIL, "blogger3")

        # Кошельки
        adv_wallet = self._setup_wallet(advertiser, INITIAL_BALANCE, "рекламодатель")
        self._setup_wallet(blogger, BLOGGER_INITIAL, "блогер")

        # Категория и площадка блогера
        category = self._get_or_create_category()
        tech_cat = self._get_or_create_tech_category()
        platform = self._get_or_create_platform(blogger, category)
        platform2 = self._get_or_create_platform2(blogger2, tech_cat)
        platform3 = self._get_or_create_platform3(blogger3, category)

        # Кампании
        campaigns = self._create_campaigns(advertiser, category)

        # ── Сценарий A: отклик pending ────────────────────────────────────────
        self.stdout.write("\n  ┌─ Сценарий A: отклик ожидает ответа")
        resp_a = self._create_response(
            blogger, campaigns[0], platform,
            price=Decimal("320_000"),
            message="Привет! Веду фитнес-блог 3 года, аудитория активная — ER 6.2%. "
                    "Готова сделать искренний обзор с личным опытом использования."
        )
        self.stdout.write(f"     Отклик #{resp_a.pk} → статус: {resp_a.status}")

        # ── Сценарий B: сделка IN_PROGRESS ───────────────────────────────────
        self.stdout.write("\n  ├─ Сценарий B: сделка в работе (деньги заморожены)")
        resp_b = self._create_response(
            blogger, campaigns[1], platform,
            price=Decimal("500_000"),
            message="YouTube-канал о дизайне, 18k подписчиков. "
                    "Готов записать детальный обзор платформы курса."
        )
        deal_b = self._accept_response(resp_b, advertiser)
        self.stdout.write(f"     Сделка #{deal_b.pk} → статус: {deal_b.status}")
        self.stdout.write(f"     Заморожено: {deal_b.amount:,.0f} UZS")

        # ── Сценарий C: сделка CHECKING ──────────────────────────────────────
        self.stdout.write("\n  ├─ Сценарий C: публикация размещена, ждёт подтверждения")
        resp_c = self._create_response(
            blogger, campaigns[2], platform,
            price=Decimal("280_000"),
            message="Instagram 12k, тематика ЗОЖ и питание. Сделаю stories + пост с промокодом."
        )
        deal_c = self._accept_response(resp_c, advertiser)
        # Имитируем публикацию
        deal_c.publication_url = "https://instagram.com/p/demo_post_123"
        deal_c.publication_at = timezone.now() - timezone.timedelta(hours=5)
        DealStatusLog.log(
            deal_c, Deal.Status.CHECKING,
            comment="Блогер разместил публикацию, ожидает подтверждения рекламодателя."
        )
        deal_c.status = Deal.Status.CHECKING
        deal_c.save(update_fields=["publication_url", "publication_at", "status", "updated_at"])
        self.stdout.write(f"     Сделка #{deal_c.pk} → статус: {deal_c.status}")
        self.stdout.write(f"     Публикация: {deal_c.publication_url}")

        # ── Сценарий D: сделка COMPLETED ─────────────────────────────────────
        self.stdout.write("\n  └─ Сценарий D: сделка завершена, деньги выплачены")
        # Создаём отдельную кампанию-шаблон (завершённая)
        completed_campaign = Campaign.objects.create(
            advertiser=advertiser,
            name="[Завершена] Реклама книжного сервиса ReadBox",
            description="Продвижение подписки на книжный сервис ReadBox.",
            category=category,
            payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("200_000"),
            budget=Decimal("1_000_000"),
            content_types=["post"],
            allowed_socials=["telegram"],
            status=Campaign.Status.COMPLETED,
        )
        resp_d = CampaignResponse.objects.create(
            blogger=blogger,
            campaign=completed_campaign,
            platform=platform,
            content_type="post",
            proposed_price=Decimal("200_000"),
            status=CampaignResponse.Status.ACCEPTED,
        )
        deal_d = Deal.objects.create(
            campaign=completed_campaign,
            blogger=blogger,
            platform=platform,
            advertiser=advertiser,
            response=resp_d,
            amount=Decimal("200_000"),
            status=Deal.Status.WAITING_PAYMENT,
            publication_url="https://t.me/demo_channel/456",
            publication_at=timezone.now() - timezone.timedelta(days=3),
        )
        DealStatusLog.log(
            deal_d, Deal.Status.COMPLETED,
            comment="Рекламодатель подтвердил публикацию. Оплата выполнена."
        )
        deal_d.status = Deal.Status.COMPLETED
        deal_d.save(update_fields=["status"])
        # Создаём транзакции вручную (сделка уже завершена, BillingService не вызываем).
        # Используем свежие объекты из БД, чтобы не перезаписать изменения reserve_funds.
        commission = (Decimal("200_000") * Decimal("15") / Decimal("100")).quantize(Decimal("0.01"))
        blogger_earning = Decimal("200_000") - commission

        fresh_adv_wallet = Wallet.objects.get(user=advertiser)
        Transaction.objects.get_or_create(
            deal=deal_d,
            type=Transaction.Type.PAYMENT,
            defaults={
                "wallet": fresh_adv_wallet,
                "amount": -Decimal("200_000"),
                "balance_after": fresh_adv_wallet.available_balance,
                "description": f"Выплата за завершённую сделку #{deal_d.pk}",
            }
        )

        blogger_wallet, _ = Wallet.objects.get_or_create(user=blogger)
        blogger_wallet.available_balance += blogger_earning
        blogger_wallet.save(update_fields=["available_balance", "updated_at"])
        Transaction.objects.get_or_create(
            deal=deal_d,
            type=Transaction.Type.EARNING,
            defaults={
                "wallet": blogger_wallet,
                "amount": blogger_earning,
                "balance_after": blogger_wallet.available_balance,
                "description": f"Заработок за сделку #{deal_d.pk} (после комиссии 15%)",
            }
        )
        self.stdout.write(f"     Сделка #{deal_d.pk} → статус: {deal_d.status}")
        self.stdout.write(f"     Блогер получил: {blogger_earning:,.0f} UZS")

        # ── Сценарий E: DirectOffer PENDING (advertiser → blogger) ───────────
        self.stdout.write("\n  ├─ Сценарий E: прямое предложение ожидает ответа блогера")
        offer_e, _ = DirectOffer.objects.get_or_create(
            advertiser=advertiser,
            campaign=campaigns[0],
            platform=platform,
            defaults={
                "blogger": blogger,
                "content_type": "post",
                "proposed_price": Decimal("300_000"),
                "message": (
                    "Здравствуйте! Ваша аудитория отлично подходит для нашего продукта FitLife. "
                    "Предлагаем разместить пост об опыте использования приложения. "
                    "Промокод для ваших подписчиков предоставим отдельно."
                ),
                "status": DirectOffer.Status.PENDING,
            }
        )
        self.stdout.write(f"     DirectOffer #{offer_e.pk} → статус: {offer_e.status} (блогер видит в дашборде)")

        # ── Сценарий F: DirectOffer ACCEPTED → сделка создана ────────────────
        self.stdout.write("\n  ├─ Сценарий F: прямое предложение принято → сделка")
        offer_f, created_f = DirectOffer.objects.get_or_create(
            advertiser=advertiser,
            campaign=campaigns[1],
            platform=platform2,
            defaults={
                "blogger": blogger2,
                "content_type": "video",
                "proposed_price": Decimal("480_000"),
                "message": (
                    "Ваш Telegram-канал про технологии идеально подходит для курса по UX/UI. "
                    "Предлагаем видео-обзор платформы."
                ),
                "status": DirectOffer.Status.ACCEPTED,
            }
        )
        if created_f:
            self._setup_wallet(blogger2, Decimal("0"), "блогер2")
            adv_w = Wallet.objects.get(user=advertiser)
            deal_f = Deal.objects.create(
                campaign=campaigns[1],
                blogger=blogger2,
                platform=platform2,
                advertiser=advertiser,
                amount=Decimal("480_000"),
                status=Deal.Status.WAITING_PAYMENT,
            )
            BillingService.reserve_funds(deal_f)
            DealStatusLog.log(
                deal_f, Deal.Status.IN_PROGRESS,
                changed_by=advertiser,
                comment="Прямое предложение принято блогером."
            )
            deal_f.status = Deal.Status.IN_PROGRESS
            deal_f.save(update_fields=["status"])
            offer_f.deal = deal_f
            offer_f.save(update_fields=["deal"])
            self.stdout.write(f"     DirectOffer #{offer_f.pk} → ACCEPTED, Сделка #{deal_f.pk} создана")
        else:
            self.stdout.write(f"     DirectOffer #{offer_f.pk} → уже существует")

        # ── Сценарий G: DirectOffer REJECTED ─────────────────────────────────
        self.stdout.write("\n  └─ Сценарий G: прямое предложение отклонено блогером")
        offer_g, _ = DirectOffer.objects.get_or_create(
            advertiser=advertiser,
            campaign=campaigns[2],
            platform=platform3,
            defaults={
                "blogger": blogger3,
                "content_type": "review",
                "proposed_price": Decimal("250_000"),
                "message": "Предлагаем разместить обзор сервиса YumBox.",
                "status": DirectOffer.Status.REJECTED,
            }
        )
        self.stdout.write(f"     DirectOffer #{offer_g.pk} → статус: {offer_g.status}")

        # ── Итог ──────────────────────────────────────────────────────────────
        adv_wallet.refresh_from_db()
        blogger_wallet.refresh_from_db()

        self.stdout.write("\n" + "─" * 55)
        self.stdout.write("✅ Демо-данные созданы!\n")
        self.stdout.write(f"  Кошелёк рекламодателя:")
        self.stdout.write(f"    Доступно:    {adv_wallet.available_balance:>12,.0f} UZS")
        self.stdout.write(f"    Заморожено:  {adv_wallet.reserved_balance:>12,.0f} UZS")
        self.stdout.write(f"  Кошелёк блогера:")
        self.stdout.write(f"    Доступно:    {blogger_wallet.available_balance:>12,.0f} UZS")
        self.stdout.write("\n  Сценарии (откликов/сделок):")
        self.stdout.write(f"    A) Отклик #{resp_a.pk}    → PENDING    (ждёт решения рекламодателя)")
        self.stdout.write(f"    B) Сделка #{deal_b.pk}    → IN_PROGRESS (деньги заморожены)")
        self.stdout.write(f"    C) Сделка #{deal_c.pk}    → CHECKING   (ждёт подтверждения)")
        self.stdout.write(f"    D) Сделка #{deal_d.pk}    → COMPLETED  (выплачено)")
        self.stdout.write("\n  Сценарии (каталог / прямые предложения):")
        self.stdout.write(f"    E) DirectOffer #{offer_e.pk} → PENDING  (блогер видит в дашборде)")
        self.stdout.write(f"    F) DirectOffer #{offer_f.pk} → ACCEPTED (сделка создана)")
        self.stdout.write(f"    G) DirectOffer #{offer_g.pk} → REJECTED (отклонено)")
        self.stdout.write("\n  Каталог блогеров (для advertiser@demo.com):")
        self.stdout.write(f"    • blogger@demo.com  — Instagram 12k, ER 6.2%")
        self.stdout.write(f"    • blogger2@demo.com — Telegram  45k, ER 4.8%")
        self.stdout.write(f"    • blogger3@demo.com — YouTube   28k, ER 3.1%")
        self.stdout.write("─" * 55 + "\n")

    # ─── Вспомогательные методы ───────────────────────────────────────────────

    def _get_user(self, email, role):
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            self.stdout.write(f"  ⚠ Пользователь {email} не найден. Запустите create_demo_users сначала.")
            raise SystemExit(1)
        return user

    def _setup_wallet(self, user, amount, label):
        wallet, created = Wallet.objects.get_or_create(user=user)
        if wallet.available_balance < Decimal("100"):
            wallet.available_balance = amount
            wallet.save(update_fields=["available_balance", "updated_at"])
            Transaction.objects.create(
                wallet=wallet,
                type=Transaction.Type.DEPOSIT,
                amount=amount,
                balance_after=amount,
                description=f"Демо-пополнение ({label})",
            )
            self.stdout.write(f"  💰 Кошелёк {label}: пополнен на {amount:,.0f} UZS")
        else:
            self.stdout.write(f"  💰 Кошелёк {label}: {wallet.available_balance:,.0f} UZS (уже есть)")
        return wallet

    def _get_or_create_extra_blogger(self, email, nickname):
        user, created = User.objects.get_or_create(
            email=email,
            defaults={"role": User.Role.BLOGGER, "status": User.Status.ACTIVE},
        )
        if created:
            user.set_password("Demo1234!")
            user.is_email_confirmed = True
            user.is_demo = True
            user.save(update_fields=["password", "is_email_confirmed", "is_demo"])
            self.stdout.write(f"  👤 Доп. блогер создан: {email}")
        # Обеспечиваем профиль с никнеймом
        from apps.profiles.models import BloggerProfile
        profile, _ = BloggerProfile.objects.get_or_create(user=user)
        if not profile.nickname:
            profile.nickname = nickname
            profile.bio = f"Демо-блогер {nickname} — автоматически созданный аккаунт для каталога."
            profile.save(update_fields=["nickname", "bio"])
            profile.check_completeness()
        return user

    def _get_or_create_category(self):
        cat, _ = Category.objects.get_or_create(
            slug="lifestyle",
            defaults={"name": "Lifestyle & ЗОЖ", "description": "Образ жизни, здоровье, фитнес"}
        )
        return cat

    def _get_or_create_tech_category(self):
        cat, _ = Category.objects.get_or_create(
            slug="tech",
            defaults={"name": "Технологии & IT", "description": "Гаджеты, программирование, стартапы"}
        )
        return cat

    def _get_or_create_platform(self, blogger, category):
        platform, created = Platform.objects.get_or_create(
            blogger=blogger,
            social_type="instagram",
            defaults={
                "url": "https://instagram.com/demo_blogger",
                "subscribers": 12000,
                "avg_views": 3500,
                "engagement_rate": Decimal("6.2"),
                "price_post": Decimal("280_000"),
                "price_stories": Decimal("150_000"),
                "status": Platform.Status.APPROVED,
            }
        )
        if created:
            platform.categories.add(category)
            self.stdout.write("  📱 Площадка блогера: создана (Instagram, 12k подп.)")
        else:
            self.stdout.write("  📱 Площадка блогера: уже существует")
        return platform

    def _get_or_create_platform2(self, blogger, category):
        platform, created = Platform.objects.get_or_create(
            blogger=blogger,
            social_type="telegram",
            defaults={
                "url": "https://t.me/demo_tech_blog",
                "subscribers": 45000,
                "avg_views": 12000,
                "engagement_rate": Decimal("4.8"),
                "price_post": Decimal("450_000"),
                "price_stories": Decimal("220_000"),
                "status": Platform.Status.APPROVED,
            }
        )
        if created:
            platform.categories.add(category)
            self.stdout.write("  📱 Площадка blogger2: создана (Telegram, 45k подп.)")
        else:
            self.stdout.write("  📱 Площадка blogger2: уже существует")
        return platform

    def _get_or_create_platform3(self, blogger, category):
        platform, created = Platform.objects.get_or_create(
            blogger=blogger,
            social_type="youtube",
            defaults={
                "url": "https://youtube.com/@demo_lifestyle",
                "subscribers": 28000,
                "avg_views": 8500,
                "engagement_rate": Decimal("3.1"),
                "price_video": Decimal("750_000"),
                "price_review": Decimal("600_000"),
                "status": Platform.Status.APPROVED,
            }
        )
        if created:
            platform.categories.add(category)
            self.stdout.write("  📱 Площадка blogger3: создана (YouTube, 28k подп.)")
        else:
            self.stdout.write("  📱 Площадка blogger3: уже существует")
        return platform

    def _create_campaigns(self, advertiser, category):
        result = []
        for data in CAMPAIGNS:
            campaign, created = Campaign.objects.get_or_create(
                advertiser=advertiser,
                name=data["name"],
                defaults={**data, "category": category},
            )
            if created:
                self.stdout.write(f"  📢 Кампания: «{campaign.name[:45]}»")
            result.append(campaign)
        return result

    def _create_response(self, blogger, campaign, platform, price, message):
        resp, _ = CampaignResponse.objects.get_or_create(
            blogger=blogger,
            campaign=campaign,
            platform=platform,
            defaults={
                "content_type": campaign.content_types[0] if campaign.content_types else "post",
                "proposed_price": price,
                "message": message,
                "status": CampaignResponse.Status.PENDING,
            }
        )
        return resp

    def _accept_response(self, response, advertiser):
        """Принимает отклик и создаёт сделку через BillingService."""
        response.status = CampaignResponse.Status.ACCEPTED
        response.save(update_fields=["status"])

        amount = response.proposed_price or response.campaign.fixed_price
        deal = Deal.objects.create(
            campaign=response.campaign,
            blogger=response.blogger,
            platform=response.platform,
            advertiser=advertiser,
            response=response,
            amount=amount,
            status=Deal.Status.WAITING_PAYMENT,
        )
        BillingService.reserve_funds(deal)
        DealStatusLog.log(
            deal, Deal.Status.IN_PROGRESS,
            changed_by=advertiser,
            comment="Отклик принят. Средства зарезервированы."
        )
        deal.status = Deal.Status.IN_PROGRESS
        deal.save(update_fields=["status"])
        return deal

    def _cleanup(self):
        self.stdout.write("🗑  Удаление старых демо-данных...")

        try:
            advertiser = User.objects.get(email=ADVERTISER_EMAIL)
            DirectOffer.objects.filter(advertiser=advertiser).delete()
            Deal.objects.filter(advertiser=advertiser).delete()
            Campaign.objects.filter(advertiser=advertiser).delete()
            Wallet.objects.filter(user=advertiser).update(
                available_balance=Decimal("0"),
                reserved_balance=Decimal("0"),
                on_withdrawal=Decimal("0"),
            )
        except User.DoesNotExist:
            pass

        for email in [BLOGGER_EMAIL, BLOGGER2_EMAIL, BLOGGER3_EMAIL]:
            try:
                blogger = User.objects.get(email=email)
                Platform.objects.filter(blogger=blogger).delete()
                Wallet.objects.filter(user=blogger).update(
                    available_balance=Decimal("0"),
                    reserved_balance=Decimal("0"),
                    on_withdrawal=Decimal("0"),
                )
            except User.DoesNotExist:
                pass

        self.stdout.write("   Готово.\n")
