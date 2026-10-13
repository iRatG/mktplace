"""
#61 (13.10.2026): ЭСФ по сделке — номер договора, ЭСФ исполнителя по категории, ручной статус сотрудника,
уведомления сторонам и сотрудникам, одно напоминание, провайдер-заглушка.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import override_settings
from django.utils import timezone

from apps.billing import esf
from apps.billing.models import EInvoice, SpecialTariff, WithdrawalRequest
from apps.billing.services import BillingService
from apps.billing.tests_deal_payouts import DealPayoutBase, approved_card
from apps.notifications.models import Notification
from apps.profiles.models import BloggerProfile

D = Decimal


class EsfTest(DealPayoutBase):
    def _ip(self):
        BloggerProfile.objects.filter(user=self.blogger).update(category=BloggerProfile.Category.IP,
                                                                 is_ip_confirmed=True)

    def test_contract_number(self):
        deal = self._deal()
        self.assertEqual(deal.contract_number, f"UB-{deal.pk}")

    def test_individual_not_required_no_notifications(self):
        deal = self._paid()
        einvoice = EInvoice.objects.get(deal=deal, kind=EInvoice.Kind.PERFORMER)
        self.assertEqual((einvoice.status, einvoice.contract_number, einvoice.amount),
                         (EInvoice.Status.NOT_REQUIRED, f"UB-{deal.pk}", D("100000")))
        self.assertFalse(Notification.objects.filter(type="esf_required").exists())
        self.assertFalse(Notification.objects.filter(type="esf_to_confirm").exists())
        self.assertEqual(deal.performer_einvoice, einvoice)

    def test_ip_waiting_with_notifications(self):
        self._ip()
        deal = self._paid()
        einvoice = EInvoice.objects.get(deal=deal, kind=EInvoice.Kind.PERFORMER)
        self.assertEqual(einvoice.status, EInvoice.Status.WAITING)
        to_blogger = Notification.objects.get(user=self.blogger, type="esf_required")
        self.assertIn(f"UB-{deal.pk}", to_blogger.body)
        self.assertIn("100 000", to_blogger.body)
        self.assertEqual(to_blogger.url, f"/deals/{deal.pk}/")
        self.assertTrue(Notification.objects.filter(user=self.adv, type="esf_to_confirm").exists())

    @override_settings(ESF_REQUIRED_CATEGORIES=["legal_entity"])
    def test_required_categories_setting(self):
        self._ip()
        deal = self._paid()
        self.assertEqual(EInvoice.objects.get(deal=deal, kind="performer").status, EInvoice.Status.NOT_REQUIRED)

    def test_open_for_deal_idempotent(self):
        deal = self._paid()
        before = EInvoice.objects.filter(deal=deal).count()
        einvoice, created = esf.open_for_deal(deal, amount=D("1"))
        self.assertFalse(created)
        self.assertEqual((EInvoice.objects.filter(deal=deal).count(), einvoice.amount), (before, D("100000")))

    def test_set_status_rejected_notifies_staff(self):
        self._ip()
        deal = self._paid()
        einvoice = EInvoice.objects.get(deal=deal, kind="performer")
        esf.set_status(einvoice.pk, self.staff, status=EInvoice.Status.MISMATCH, note="Сумма не совпадает")
        note = Notification.objects.get(user=self.staff, type="esf_problem")
        self.assertIn("расхождение", note.body)
        self.assertIn("Сумма не совпадает", note.body)
        self.assertEqual(note.url, f"/panel/withdrawals/?deal={deal.pk}")
        payout = WithdrawalRequest.objects.get(deal=deal)
        self.assertIn("ЭСФ: расхождение", payout.blocked_reasons)

    def test_confirmed_requires_number_and_platform_status_rules(self):
        self._ip()
        deal = self._paid()
        einvoice = EInvoice.objects.get(deal=deal, kind="performer")
        with self.assertRaisesMessage(esf.EsfError, "номер"):
            esf.set_status(einvoice.pk, self.staff, status=EInvoice.Status.CONFIRMED)
        with self.assertRaisesMessage(esf.EsfError, "Недопустимый"):
            esf.set_status(einvoice.pk, self.staff, status=EInvoice.Status.TO_ISSUE)
        esf.set_status(einvoice.pk, self.staff, status=EInvoice.Status.CONFIRMED, number="ЭСФ-77")
        einvoice.refresh_from_db()
        self.assertEqual((einvoice.status, einvoice.number, einvoice.checked_by), ("confirmed", "ЭСФ-77", self.staff))

    def test_platform_einvoice_only_with_commission(self):
        SpecialTariff.objects.create(advertiser=self.adv, percent=D("0"))
        approved_card(self.blogger)
        deal = self._paid()
        BillingService.complete_withdrawal(WithdrawalRequest.objects.get(deal=deal), comment="п/п")
        self.assertFalse(EInvoice.objects.filter(deal=deal, kind=EInvoice.Kind.PLATFORM).exists())

    def test_manual_provider_sync_does_nothing(self):
        self._ip()
        self._paid()
        self.assertEqual(esf.sync_waiting(), 0)
        self.assertIsNone(esf.get_esf_provider("manual").fetch_status(None))
        self.assertIsNone(esf.get_esf_provider("didox"))

    def test_reminder_once(self):
        self._ip()
        deal = self._paid()
        self.assertEqual(esf.send_reminders(), 0)
        EInvoice.objects.filter(deal=deal).update(created_at=timezone.now() - timedelta(days=4))
        self.assertEqual(esf.send_reminders(), 1)
        self.assertEqual(esf.send_reminders(), 0)
        reminders = Notification.objects.filter(user=self.adv, type="esf_to_confirm", title__contains="напоминание")
        self.assertEqual(reminders.count(), 1)
