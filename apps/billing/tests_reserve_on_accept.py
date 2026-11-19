"""Р12 (#69): резерв в момент Акцепта (ПЭТ 6.3, 7.1; ПР 5.2–5.5, редакция 06–07.11.2026).

Направление оферты денег не резервирует — только проверяет доступный баланс. Резерв создаётся при Акцепте двумя
частями (стоимость услуг и комиссия); не хватает денег — Акцепт не регистрируется, рекламодателю — просьба пополнить
баланс. Оферты, зарезервированные при направлении (09.10–14.11), доживают по-старому.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import Transaction, Wallet
from apps.billing.services import BillingService
from apps.campaigns.models import Campaign, DirectOffer
from apps.campaigns.services import (
    AcceptError, accept_direct_offer, complete_campaign, expire_overdue_responses_and_offers, reject_offer, send_offer,
)
from apps.campaigns.testing import CARD_FIELDS, publication_day
from apps.campaigns.validation import budget_usage, deals_in_cap
from apps.deals.models import Deal
from apps.notifications.models import Notification
from apps.platforms.models import Platform
from apps.users.models import User

D = Decimal
_n = 0


def _blogger(email):
    global _n
    _n += 1
    user = User.objects.create_user(email=email, password="pass1234", role=User.Role.BLOGGER, status=User.Status.ACTIVE)
    Platform.objects.create(blogger=user, social_type=Platform.SocialType.INSTAGRAM, url=f"https://instagram.com/roa{_n}",
                            subscribers=12000, price_post=D("50000"), status=Platform.Status.APPROVED)
    return user


class ReserveOnAcceptBase(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.adv = User.objects.create_user(email="adv@test.com", password="pass1234", role=User.Role.ADVERTISER,
                                            status=User.Status.ACTIVE)
        self._balance("1000000")
        self.campaign = Campaign.objects.create(
            advertiser=self.adv, name="Кампания", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=D("100000"), budget=D("1000000"), status=Campaign.Status.ACTIVE, max_bloggers=5,
            start_date=today, end_date=today + timedelta(days=30), **CARD_FIELDS,
        )
        self.blogger = _blogger("bl@test.com")

    def _balance(self, available, reserved="0"):
        Wallet.objects.filter(user=self.adv).update(available_balance=D(available), reserved_balance=D(reserved))

    def _wallet(self):
        return Wallet.objects.get(user=self.adv)

    def _offer(self, blogger=None):
        blogger = blogger or self.blogger
        return send_offer(campaign_pk=self.campaign.pk, advertiser=self.adv, blogger=blogger,
                          platform=Platform.objects.get(blogger=blogger), publication_date=publication_day(self.campaign))


class SendOfferTest(ReserveOnAcceptBase):
    def test_offer_reserves_nothing(self):
        offer = self._offer()
        wallet = self._wallet()
        self.assertEqual((wallet.available_balance, wallet.reserved_balance), (D("1000000"), D("0")))
        self.assertFalse(Transaction.objects.filter(type=Transaction.Type.RESERVE).exists())
        self.assertTrue(offer.reserve_on_accept)
        self.assertIsNone(offer.reserved_at)
        # Сумма и комиссия зафиксированы в оферте: 100 000 + 10% (тариф «Бесплатно»).
        self.assertEqual((offer.reserved_amount, offer.reserved_commission, offer.reserved_total),
                         (D("100000"), D("10000"), D("110000")))

    def test_offer_needs_available_balance(self):
        self._balance("100000")
        with self.assertRaisesMessage(AcceptError, "Недостаточно средств на балансе: для оферты нужно 110 000"):
            self._offer()
        self.assertFalse(DirectOffer.objects.exists())

    def test_pending_offer_holds_campaign_place_and_budget(self):
        self._offer()
        self.assertEqual(deals_in_cap(self.campaign), 1)
        self.assertEqual(budget_usage([self.campaign])[self.campaign.pk]["offers"], D("100000"))


class AcceptTest(ReserveOnAcceptBase):
    def test_accept_creates_two_reserves_and_deal(self):
        offer = self._offer()
        deal = accept_direct_offer(offer.pk, self.blogger)
        wallet = self._wallet()
        self.assertEqual((wallet.available_balance, wallet.reserved_balance), (D("890000"), D("110000")))
        reserves = list(Transaction.objects.filter(type=Transaction.Type.RESERVE).order_by("pk"))
        self.assertEqual([r.amount for r in reserves], [D("-100000"), D("-10000")])
        self.assertTrue(all(r.deal_id == deal.pk and r.offer_id == offer.pk for r in reserves))
        self.assertIn("стоимости услуг", reserves[0].description)
        self.assertIn("комиссии", reserves[1].description)
        self.assertEqual((deal.amount, deal.commission_percent, deal.commission_amount, deal.reserved_total),
                         (D("100000"), D("10"), D("10000"), D("110000")))
        offer.refresh_from_db()
        self.assertEqual(offer.status, DirectOffer.Status.ACCEPTED)
        self.assertIsNotNone(offer.reserved_at)

    def test_accept_without_money_is_not_registered(self):
        offer = self._offer()
        self._balance("50000")  # рекламодатель потратил деньги после направления оферты
        with self.assertRaisesMessage(AcceptError, "Акцепт не зарегистрирован"):
            accept_direct_offer(offer.pk, self.blogger)
        self.assertFalse(Deal.objects.exists())
        self.assertFalse(Transaction.objects.filter(type=Transaction.Type.RESERVE).exists())
        offer.refresh_from_db()
        self.assertEqual(offer.status, DirectOffer.Status.PENDING)
        self.assertEqual(self._wallet().available_balance, D("50000"))
        note = Notification.objects.get(user=self.adv, type=Notification.Type.DIRECT_OFFER_NEEDS_FUNDS)
        self.assertIn("110 000", note.body)
        self.assertEqual(note.url, reverse("web:wallet"))
        # Пополнил баланс — тот же Акцепт проходит.
        self._balance("200000")
        deal = accept_direct_offer(offer.pk, self.blogger)
        self.assertEqual(deal.status, Deal.Status.IN_PROGRESS)

    def test_two_offers_one_balance(self):
        """Две оферты на один баланс: первая принятая резервирует, на вторую денег уже нет."""
        self._balance("150000")
        other = _blogger("bl2@test.com")
        first, second = self._offer(), self._offer(other)
        accept_direct_offer(first.pk, self.blogger)
        with self.assertRaises(AcceptError):
            accept_direct_offer(second.pk, other)
        wallet = self._wallet()
        self.assertEqual((wallet.available_balance, wallet.reserved_balance), (D("40000"), D("110000")))

    def test_metrics_after_full_payment_match_reserve(self):
        from apps.billing import metrics

        offer = self._offer()
        deal = accept_direct_offer(offer.pk, self.blogger)
        BillingService.complete_deal_payment(deal)
        self.assertEqual(metrics.platform_revenue(), D("20000"))  # комиссия 10 000 + плата исполнителя 10 000 (по умолчанию 10%)
        self.assertEqual(metrics.advertiser_spent(self.adv), D("110000"))
        wallet = self._wallet()
        self.assertEqual((wallet.available_balance, wallet.reserved_balance), (D("890000"), D("0")))


class NoReserveNoReleaseTest(ReserveOnAcceptBase):
    def test_reject_expire_and_campaign_end_move_no_money(self):
        first = self._offer()
        reject_offer(first.pk, self.blogger)
        second = self._offer(_blogger("bl2@test.com"))
        DirectOffer.objects.filter(pk=second.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        expire_overdue_responses_and_offers()
        self._offer(_blogger("bl3@test.com"))
        complete_campaign(self.campaign.pk, actor=self.adv)
        self.assertFalse(Transaction.objects.filter(type__in=[Transaction.Type.RESERVE, Transaction.Type.RELEASE]).exists())
        wallet = self._wallet()
        self.assertEqual((wallet.available_balance, wallet.reserved_balance), (D("1000000"), D("0")))
        self.assertEqual(DirectOffer.objects.filter(status=DirectOffer.Status.PENDING).count(), 0)


class LegacyReservedOfferTest(ReserveOnAcceptBase):
    """Оферта, направленная 09.10–14.11, зарезервирована при направлении — доживает по-старому."""

    def _legacy_offer(self, blogger=None):
        offer = self._offer(blogger)
        DirectOffer.objects.filter(pk=offer.pk).update(reserve_on_accept=False, reserved_at=timezone.now())
        offer.refresh_from_db()
        BillingService.reserve_for_offer(offer)
        return offer

    def test_reject_returns_reserve(self):
        offer = self._legacy_offer()
        self.assertEqual(self._wallet().reserved_balance, D("110000"))
        reject_offer(offer.pk, self.blogger)
        wallet = self._wallet()
        self.assertEqual((wallet.available_balance, wallet.reserved_balance), (D("1000000"), D("0")))

    def test_accept_does_not_reserve_twice(self):
        offer = self._legacy_offer()
        deal = accept_direct_offer(offer.pk, self.blogger)
        self.assertEqual(Transaction.objects.filter(type=Transaction.Type.RESERVE).count(), 1)
        self.assertEqual(Transaction.objects.get(type=Transaction.Type.RESERVE).deal_id, deal.pk)
        wallet = self._wallet()
        self.assertEqual((wallet.available_balance, wallet.reserved_balance), (D("890000"), D("110000")))


class WalletShowsTwoReservesTest(ReserveOnAcceptBase):
    def test_wallet_splits_reserve(self):
        offer = self._offer()
        accept_direct_offer(offer.pk, self.blogger)
        self.client.force_login(self.adv)
        page = self.client.get(reverse("web:wallet")).content.decode()
        self.assertIn("data-reserve-services", page)
        self.assertIn("data-reserve-commission", page)
        self.assertIn("100 000", page)
        self.assertIn("10 000", page)
