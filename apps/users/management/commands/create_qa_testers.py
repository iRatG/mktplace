"""
create_qa_testers — создаёт QA-аккаунты для внешних тестировщиков
(один блогер, один рекламодатель) и выдаёт им тестовый баланс.

В отличие от create_demo_users (для презентации заказчику), эти аккаунты
предназначены для ручного тестирования сценариев живыми людьми — они
регистрируются как demo (is_demo=True), баланс выдаётся через штатный
BillingService.grant_test_balance (аудируется в TestBalanceGrant), профиль
и площадки/кампании тестировщики создают сами по ходу сценариев.

Запуск:
    python manage.py create_qa_testers            # создать
    python manage.py create_qa_testers --reset    # удалить и пересоздать
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.billing.services import BillingService
from apps.users.models import User

QA_TESTERS = [
    {
        "email": "blog_test_1@demo.com",
        "password": "Test1234!",
        "role": User.Role.BLOGGER,
        "label": "QA-тестировщик (блогер)",
    },
    {
        "email": "adv_test_2@demo.com",
        "password": "Test1234!",
        "role": User.Role.ADVERTISER,
        "label": "QA-тестировщик (рекламодатель)",
    },
]

TEST_BALANCE = Decimal("100_000_000")  # 100 млн UZS


def _delete_qa_tester(user):
    from apps.deals.models import Deal, DealStatusLog
    from apps.campaigns.models import Campaign, Response, DirectOffer
    from apps.platforms.models import Platform
    from apps.billing.models import Wallet, Transaction, WithdrawalRequest, TestBalanceGrant

    deals = Deal.objects.filter(advertiser=user) | Deal.objects.filter(blogger=user)
    for deal in deals:
        DealStatusLog.objects.filter(deal=deal).delete()
        Transaction.objects.filter(deal=deal).delete()
    deals.delete()

    Response.objects.filter(blogger=user).delete()
    DirectOffer.objects.filter(advertiser=user).delete()
    DirectOffer.objects.filter(blogger=user).delete()
    Campaign.objects.filter(advertiser=user).delete()
    Platform.objects.filter(blogger=user).delete()

    TestBalanceGrant.objects.filter(user=user).delete()
    Transaction.objects.filter(wallet__user=user).delete()
    Wallet.objects.filter(user=user).delete()
    WithdrawalRequest.objects.filter(blogger=user).delete()

    user.delete()


class Command(BaseCommand):
    help = "Создаёт QA-аккаунты для внешних тестировщиков (blog_test_1, adv_test_2) с тестовым балансом"

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Удалить и пересоздать существующие QA-аккаунты",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("Создание QA-аккаунтов тестировщиков...\n"))

        granted_by = User.objects.filter(is_staff=True, is_superuser=True).order_by("pk").first()
        if not granted_by:
            self.stderr.write(self.style.ERROR(
                "Нет ни одного superuser-аккаунта для granted_by. "
                "Сначала запустите create_demo_users или создайте суперюзера."
            ))
            return

        for data in QA_TESTERS:
            email = data["email"]
            existing = User.objects.filter(email=email).first()

            if existing:
                if options["reset"]:
                    _delete_qa_tester(existing)
                    self.stdout.write(f"  Удалён: {email}")
                else:
                    self.stdout.write(self.style.WARNING(f"  Уже существует (пропущен): {email}"))
                    continue

            user = User.objects.create_user(
                email=email,
                password=data["password"],
                role=data["role"],
            )
            user.is_email_confirmed = True
            user.is_demo = True
            user.status = User.Status.ACTIVE
            user.save(update_fields=["is_email_confirmed", "is_demo", "status"])

            BillingService.grant_test_balance(
                user=user,
                amount=TEST_BALANCE,
                granted_by=granted_by,
                note="QA tester onboarding grant",
            )

            self.stdout.write(self.style.SUCCESS(
                f"  ✓ {data['label']:28s} {email}  /  {data['password']}  "
                f"(+{TEST_BALANCE:,.0f} UZS)"
            ))

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Готово! Данные для входа QA-тестировщиков:\n"))
        self.stdout.write(f"  {'Роль':<28} {'Email':<25} {'Пароль'}")
        self.stdout.write(f"  {'-'*70}")
        for data in QA_TESTERS:
            self.stdout.write(f"  {data['label']:<28} {data['email']:<25} {data['password']}")
        self.stdout.write("")
