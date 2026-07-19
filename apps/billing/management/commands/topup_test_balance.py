"""
topup_test_balance — доводит тестовый баланс существующих demo-аккаунтов
рекламодателей до нового лимита TestBalanceGrant.MAX_TOTAL.

Только для is_demo=True аккаунтов — обычный workflow BillingService.grant_test_balance.
Используется QA-командой для тестирования функционала, лимит не действует
на реальные (не demo) счета.

Запуск:
    python manage.py topup_test_balance
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import models

from apps.billing.models import TestBalanceGrant
from apps.billing.services import BillingService
from apps.users.models import User


class Command(BaseCommand):
    help = "Доводит тестовый баланс существующих demo-рекламодателей до лимита TestBalanceGrant.MAX_TOTAL"

    def handle(self, *args, **options):
        granted_by = User.objects.filter(is_staff=True, is_superuser=True).order_by("pk").first()
        if not granted_by:
            self.stderr.write(self.style.ERROR("Нет ни одного superuser-аккаунта для granted_by."))
            return

        limit = Decimal(str(TestBalanceGrant.MAX_TOTAL))
        demo_advertisers = User.objects.filter(is_demo=True, role=User.Role.ADVERTISER)

        if not demo_advertisers.exists():
            self.stdout.write(self.style.WARNING("Demo-рекламодателей не найдено."))
            return

        for user in demo_advertisers:
            already_granted = (
                TestBalanceGrant.objects.filter(user=user)
                .aggregate(total=models.Sum("amount"))["total"]
                or Decimal("0")
            )
            remaining = limit - already_granted
            if remaining <= 0:
                self.stdout.write(f"  {user.email}: уже на лимите ({already_granted})")
                continue

            BillingService.grant_test_balance(
                user=user,
                amount=remaining,
                granted_by=granted_by,
                note="Top-up to new QA test balance limit (2026-07-19)",
            )
            self.stdout.write(
                self.style.SUCCESS(f"  {user.email}: +{remaining} (итого {limit})")
            )

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Готово."))
