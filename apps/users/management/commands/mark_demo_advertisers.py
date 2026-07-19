"""
mark_demo_advertisers — помечает конкретных рекламодателей флагом is_demo=True,
чтобы им можно было начислять тестовый баланс через BillingService.grant_test_balance
(этот механизм требует is_demo=True — иначе баланс не начислить без реального платежа).

Используется для реальных тестировщиков, зарегистрировавшихся обычной регистрацией,
которым нужен тестовый баланс для проверки функционала.

is_demo=True не блокирует ничего для рекламодателя (блок вывода средств касается
только блогеров) — эффект чисто в разрешении на TestBalanceGrant.

Запуск:
    python manage.py mark_demo_advertisers user1@example.com user2@example.com
"""

from django.core.management.base import BaseCommand, CommandError

from apps.users.models import User


class Command(BaseCommand):
    help = "Помечает указанных рекламодателей is_demo=True для начисления тестового баланса"

    def add_arguments(self, parser):
        parser.add_argument("emails", nargs="+", help="Email(ы) рекламодателей")

    def handle(self, *args, **options):
        for email in options["emails"]:
            try:
                user = User.objects.get(email=email)
            except User.DoesNotExist:
                raise CommandError(f"Пользователь не найден: {email}")

            if user.role != User.Role.ADVERTISER:
                self.stdout.write(self.style.WARNING(f"  {email}: пропущен (роль {user.role}, не advertiser)"))
                continue

            if user.is_demo:
                self.stdout.write(f"  {email}: уже is_demo=True")
                continue

            user.is_demo = True
            user.save(update_fields=["is_demo"])
            self.stdout.write(self.style.SUCCESS(f"  {email}: is_demo=True"))
