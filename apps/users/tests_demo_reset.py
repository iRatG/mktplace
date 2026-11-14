"""
Сброс демо-данных (живой прогон 11.10): create_demo_users --reset не падает на журнале доступа к ПД, seed_demo_data
создаёт ожидающую оферту обычным путём — с резервом.
"""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.campaigns.models import DirectOffer
from apps.users.models import PersonalDataAccess, User


def _run(*args):
    call_command(*args, stdout=StringIO())


class DemoResetTest(TestCase):
    def test_reset_keeps_staff_and_journal(self):
        _run("create_demo_users")
        admin = User.objects.get(email="admin@demo.com")
        PersonalDataAccess.objects.create(staff=admin, action="Открыт файл")

        _run("create_demo_users", "--reset")

        admin.refresh_from_db()
        self.assertTrue(admin.check_password("Demo1234!"))
        self.assertTrue(admin.is_staff and admin.is_demo)
        self.assertEqual(PersonalDataAccess.objects.filter(staff=admin).count(), 1)

    def test_seed_offer_has_amount_reserved_on_accept(self):
        _run("create_demo_users")
        _run("seed_demo_data")
        offer = DirectOffer.objects.get(status=DirectOffer.Status.PENDING, response__isnull=True,
                                        advertiser__email="advertiser@demo.com")
        self.assertTrue(offer.reserve_on_accept)  # резерв — при Акцепте (ПР 5.4)
        self.assertEqual(offer.reserved_amount, offer.proposed_price)
        self.assertIsNotNone(offer.commission_percent)

    def test_reset_after_seed_with_einvoice(self):
        """Демо-сделка с ЭСФ (PROTECT на сделку) не мешает сбросу демо-аккаунтов."""
        from apps.billing.models import EInvoice
        from apps.deals.models import Deal

        _run("create_demo_users")
        _run("seed_demo_data")
        deal = Deal.objects.filter(advertiser__email="advertiser@demo.com").first()
        self.assertIsNotNone(deal)
        EInvoice.objects.get_or_create(deal=deal, kind=EInvoice.Kind.PERFORMER,
                                       defaults={"status": EInvoice.Status.WAITING, "amount": deal.amount})
        _run("create_demo_users", "--reset")
        self.assertFalse(Deal.objects.filter(pk=deal.pk).exists())
        self.assertTrue(User.objects.filter(email="advertiser@demo.com").exists())
