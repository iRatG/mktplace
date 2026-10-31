"""Пометка про НДС рядом с комиссией и оборот с начала года к лимиту налога с оборота."""
from datetime import datetime
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.billing import metrics
from apps.billing.models import Transaction, Wallet
from apps.users.models import User


def _user(email, **extra):
    return User.objects.create_user(email=email, password="x", role=User.Role.ADVERTISER,
                                    status=User.Status.ACTIVE, **extra)


def _payment(user, amount, when=None, kind=Transaction.Type.PAYMENT):
    wallet = Wallet.objects.get(user=user)
    row = Transaction.objects.create(wallet=wallet, type=kind, amount=-Decimal(amount), balance_after=0)
    if when:
        Transaction.objects.filter(pk=row.pk).update(created_at=when)
    return row


@override_settings(TURNOVER_LIMIT_BRV=10, BRV_AMOUNT=100, TURNOVER_WARNING_PERCENT=80)
class TurnoverLimitTests(TestCase):
    def setUp(self):
        self.adv = _user("adv@test.local")

    def test_counts_this_year_only(self):
        year = timezone.localdate().year
        _payment(self.adv, 300)
        _payment(self.adv, 700, timezone.make_aware(datetime(year - 1, 12, 31, 23, 0)))
        self.assertEqual(metrics.year_turnover(), Decimal("300"))
        self.assertEqual(metrics.year_turnover(year - 1), Decimal("700"))

    def test_demo_and_other_types_excluded(self):
        _payment(_user("demo@test.local", is_demo=True), 500)
        _payment(self.adv, 100, kind=Transaction.Type.RESERVE)
        self.assertEqual(metrics.year_turnover(), Decimal("0"))

    def test_levels(self):
        _payment(self.adv, 799)
        status = metrics.turnover_limit()
        self.assertEqual(status["limit"], Decimal("1000"))
        self.assertEqual(status["percent"], Decimal("79.9"))
        self.assertFalse(status["near"])

        _payment(self.adv, 1)
        self.assertTrue(metrics.turnover_limit()["near"])
        self.assertFalse(metrics.turnover_limit()["over"])

        _payment(self.adv, 201)
        status = metrics.turnover_limit()
        self.assertTrue(status["over"])
        self.assertEqual(status["bar"], 100)

    def _dashboard(self):
        self.client.force_login(_user("staff@test.local", is_staff=True))
        return self.client.get(reverse("web:admin_dashboard"))

    def test_dashboard_quiet_below_threshold(self):
        response = self._dashboard()
        self.assertContains(response, "data-turnover-limit")
        self.assertNotContains(response, "data-turnover-warning")

    def test_dashboard_warns_near_limit(self):
        _payment(self.adv, 900)
        self.assertContains(self._dashboard(), "Оборот близок к лимиту")

    def test_dashboard_warns_over_limit(self):
        _payment(self.adv, 1500)
        self.assertContains(self._dashboard(), "Лимит превышен")


class VatNoteTests(TestCase):
    def test_default_note_on_public_pages(self):
        for name in ("web:landing", "web:faq", "web:oferta"):
            self.assertContains(self.client.get(reverse(name)), "НДС не облагается")

    @override_settings(VAT_NOTE="в т.ч. НДС 6%")
    def test_note_comes_from_setting(self):
        response = self.client.get(reverse("web:faq"))
        self.assertContains(response, "в т.ч. НДС 6%")
        self.assertNotContains(response, "НДС не облагается")
