from django.core.management.base import BaseCommand, CommandError

from apps.users.models import BlockedIP


class Command(BaseCommand):
    help = (
        "Снять блокировку с IP (например, если админ случайно заблокировал сам себя "
        "и не может войти в Django admin). Без аргументов показывает действующие блокировки."
    )

    def add_arguments(self, parser):
        parser.add_argument("ip", nargs="?", help="IP-адрес, который нужно разблокировать")

    def handle(self, *args, ip=None, **options):
        if ip is None:
            active = [b for b in BlockedIP.objects.all() if b.is_active]
            if not active:
                self.stdout.write("Действующих блокировок нет.")
            for b in active:
                until = b.blocked_until.strftime("%Y-%m-%d %H:%M") if b.blocked_until else "бессрочно"
                self.stdout.write(f"{b.ip}\t{b.get_source_display()}\tдо {until}\t{b.reason}")
            return
        deleted, _ = BlockedIP.objects.filter(ip=ip).delete()
        if not deleted:
            raise CommandError(f"IP {ip} в списке блокировок не найден.")
        self.stdout.write(self.style.SUCCESS(f"IP {ip} разблокирован."))
