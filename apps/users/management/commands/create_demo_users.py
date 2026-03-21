from django.core.management.base import BaseCommand
from django.db import transaction

from apps.users.models import User


def _delete_user_safely(user):
    """Delete a demo user and all their dependent data in correct order."""
    from apps.deals.models import Deal, DealStatusLog
    from apps.campaigns.models import Campaign, Response
    from apps.platforms.models import Platform
    from apps.billing.models import Wallet, Transaction, WithdrawalRequest
    from apps.profiles.models import AdvertiserProfile, BloggerProfile

    # Delete deals (PROTECT FK) — must go before campaigns and users
    deals = Deal.objects.filter(advertiser=user) | Deal.objects.filter(blogger=user)
    for deal in deals:
        DealStatusLog.objects.filter(deal=deal).delete()
        Transaction.objects.filter(deal=deal).delete()
    deals.delete()

    # Delete campaigns (PROTECT FK on advertiser)
    Campaign.objects.filter(advertiser=user).delete()

    # Delete platforms (CASCADE but explicit to be safe)
    Platform.objects.filter(blogger=user).delete()

    # Delete wallet and transactions
    Wallet.objects.filter(user=user).delete()
    WithdrawalRequest.objects.filter(blogger=user).delete()

    user.delete()


DEMO_USERS = [
    {
        "email": "admin@demo.com",
        "password": "Demo1234!",
        "role": User.Role.ADVERTISER,
        "is_staff": True,
        "is_superuser": True,
        "label": "Админ (клиент)",
    },
    {
        "email": "advertiser@demo.com",
        "password": "Demo1234!",
        "role": User.Role.ADVERTISER,
        "is_staff": False,
        "is_superuser": False,
        "label": "Рекламодатель",
    },
    {
        "email": "blogger@demo.com",
        "password": "Demo1234!",
        "role": User.Role.BLOGGER,
        "is_staff": False,
        "is_superuser": False,
        "label": "Блогер",
    },
]


class Command(BaseCommand):
    help = "Создаёт демо-аккаунты для презентации проекта заказчику"

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Удалить и пересоздать существующие демо-аккаунты",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("Создание демо-аккаунтов...\n"))

        for data in DEMO_USERS:
            email = data["email"]
            existing = User.objects.filter(email=email).first()

            if existing:
                if options["reset"]:
                    _delete_user_safely(existing)
                    self.stdout.write(f"  Удалён: {email}")
                else:
                    self.stdout.write(
                        self.style.WARNING(f"  Уже существует (пропущен): {email}")
                    )
                    continue

            user = User.objects.create_user(
                email=email,
                password=data["password"],
                role=data["role"],
            )
            user.is_staff = data["is_staff"]
            user.is_superuser = data["is_superuser"]
            user.is_email_confirmed = True
            user.status = User.Status.ACTIVE
            user.save(update_fields=["is_staff", "is_superuser", "is_email_confirmed", "status"])

            self.stdout.write(
                self.style.SUCCESS(f"  ✓ {data['label']:20s}  {email}  /  {data['password']}")
            )

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Готово! Данные для входа:\n"))
        self.stdout.write(f"  {'Роль':<20} {'Email':<25} {'Пароль'}")
        self.stdout.write(f"  {'-'*60}")
        for data in DEMO_USERS:
            self.stdout.write(f"  {data['label']:<20} {data['email']:<25} {data['password']}")
        self.stdout.write("")
        self.stdout.write(f"  Админ-панель: /admin/")
