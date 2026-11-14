"""Р13 (#74): две платы оператора (ПР 7.1–7.7, ПЭТ 6.5, ТО 4.3, 10.3; редакция 06–07.11.2026).

Комиссия рекламодателя — сверх стоимости услуг, резервируется отдельно; плата исполнителя — внутри стоимости,
удерживается зачётом при выплате. Обе фиксируются в оферте и сделке; при частичной оплате — от принятой части; при одной
компенсации — ни той, ни другой. Ставка исполнителя по умолчанию — CONTRACTOR_FEE_PERCENT (до ответа бизнеса — 0).
"""
from datetime import timedelta
from decimal import Decimal

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.billing import metrics
from apps.billing.models import ContractorFeeRate, EInvoice, Transaction, Wallet, WithdrawalRequest
from apps.billing.services import BillingService
from apps.billing.tests_deal_payouts import DealPayoutBase, approved_card
from apps.campaigns.models import DirectOffer
from apps.deals.models import Deal

D = Decimal


@override_settings(CONTRACTOR_FEE_PERCENT="10")
class TwoFeesTest(DealPayoutBase):
    def test_offer_and_deal_fix_both_fees(self):
        deal = self._deal()
        offer = DirectOffer.objects.get(deal=deal)
        self.assertEqual((offer.commission_percent, offer.reserved_commission), (D("16"), D("16000")))
        self.assertEqual((offer.contractor_fee_percent, offer.contractor_fee_amount, offer.contractor_payout),
                         (D("10"), D("10000"), D("90000")))
        self.assertTrue(deal.two_fees)
        self.assertEqual((deal.contractor_fee_percent, deal.contractor_fee_amount, deal.blogger_earning),
                         (D("10"), D("10000"), D("90000")))
        # Резерв рекламодателя — стоимость + его комиссия; плата исполнителя в резерв не добавляется.
        self.assertEqual(deal.reserved_total, D("116000"))

    def test_full_payment_withholds_contractor_fee(self):
        approved_card(self.blogger)
        deal = self._paid()
        self.assertEqual(deal.contractor_fee_withheld, D("10000"))
        self.assertEqual(Transaction.objects.get(deal=deal, type=Transaction.Type.PAYMENT).amount, D("-116000"))
        self.assertEqual(Transaction.objects.get(deal=deal, type=Transaction.Type.EARNING).amount, D("90000"))
        self.assertEqual(WithdrawalRequest.objects.get(deal=deal).amount, D("90000"))
        self.assertEqual(metrics.platform_revenue(), D("26000"))  # комиссия 16 000 + плата 10 000
        self.assertEqual(metrics.deal_commission(deal), D("16000"))
        self.assertEqual(metrics.deal_contractor_fee(deal), D("10000"))
        adv = Wallet.objects.get(user=self.adv)
        self.assertEqual((adv.available_balance, adv.reserved_balance), (D("884000"), D("0")))

    def test_performer_einvoice_on_full_price(self):
        from apps.profiles.models import BloggerProfile

        BloggerProfile.objects.filter(user=self.blogger).update(category=BloggerProfile.Category.IP, is_ip_confirmed=True)
        approved_card(self.blogger)
        deal = self._paid()
        einvoice = EInvoice.objects.get(deal=deal, kind=EInvoice.Kind.PERFORMER)
        self.assertEqual(einvoice.amount, D("100000"))  # ПР 8.6: на стоимость до удержаний

    def test_split_recalculates_both_fees_on_accepted_part(self):
        deal = self._deal()
        BillingService.split_deal_payment(deal, D("40000"))
        deal.refresh_from_db()
        self.assertEqual(deal.contractor_fee_withheld, D("4000"))
        self.assertEqual(Transaction.objects.get(deal=deal, type=Transaction.Type.PAYMENT).amount, D("-46400"))
        self.assertEqual(Transaction.objects.get(deal=deal, type=Transaction.Type.EARNING).amount, D("36000"))
        self.assertEqual(Wallet.objects.get(user=self.adv).available_balance, D("884000") + D("69600"))
        self.assertEqual(metrics.platform_revenue(), D("10400"))

    def test_compensation_only_has_no_fees(self):
        deal = self._deal()
        BillingService.split_deal_payment(deal, D("30000"), compensation=True)
        deal.refresh_from_db()
        self.assertEqual(deal.contractor_fee_withheld, D("0"))
        self.assertEqual(Transaction.objects.get(deal=deal, type=Transaction.Type.PAYMENT).amount, D("-30000"))
        self.assertEqual(Transaction.objects.get(deal=deal, type=Transaction.Type.EARNING).amount, D("30000"))
        self.assertEqual(metrics.platform_revenue(), D("0"))
        self.assertEqual(Wallet.objects.get(user=self.adv).available_balance, D("884000") + D("86000"))

    def test_individual_rate_and_fixed_in_deal(self):
        ContractorFeeRate.objects.create(blogger=self.blogger, percent=D("5"), note="Соглашение")
        deal = self._deal()
        self.assertEqual((deal.contractor_fee_percent, deal.contractor_fee_amount), (D("5"), D("5000")))
        # Ставка изменилась после заключения — сделка не меняется (ПР 7.4).
        ContractorFeeRate.objects.filter(blogger=self.blogger).update(percent=D("20"))
        deal.refresh_from_db()
        self.assertEqual(deal.blogger_earning, D("95000"))

    def test_expired_individual_rate_falls_back_to_default(self):
        ContractorFeeRate.objects.create(blogger=self.blogger, percent=D("5"),
                                         valid_until=timezone.localdate() - timedelta(days=1))
        self.assertEqual(self._deal().contractor_fee_percent, D("10"))

    def test_blogger_sees_fee_before_acceptance_and_in_deal(self):
        from apps.campaigns.models import Response as CampaignResponse
        from apps.campaigns.services import accept_response
        from apps.campaigns.testing import publication_day
        from apps.platforms.models import Platform

        platform = Platform.objects.create(blogger=self.blogger, social_type=Platform.SocialType.INSTAGRAM,
                                           url="https://instagram.com/twofees", subscribers=1000,
                                           price_post=D("50000"), status=Platform.Status.APPROVED)
        from apps.profiles.testing import contracted_before_rule

        with contracted_before_rule(self.blogger):
            resp = CampaignResponse.objects.create(campaign=self.campaign, blogger=self.blogger, platform=platform,
                                                   content_type="post", proposed_price=D("100000"))
            accept_response(resp.pk, self.adv, publication_day(self.campaign))
            self.client.force_login(self.blogger)
            page = self.client.get(reverse("web:blogger_dashboard")).content.decode()
        self.assertIn("data-offer-contractor-fee", page)
        self.assertIn("к выплате 90 000", page)

        deal = self._deal()
        self.client.force_login(self.blogger)
        self.assertContains(self.client.get(reverse("web:deal_detail", kwargs={"pk": deal.pk})), "data-contractor-fee")
        self.client.force_login(self.adv)
        self.assertNotContains(self.client.get(reverse("web:deal_detail", kwargs={"pk": deal.pk})), "data-contractor-fee")


class DefaultZeroFeeTest(DealPayoutBase):
    """До ответа бизнеса (В21) плата исполнителя — 0%: исполнитель получает цену целиком, как раньше."""

    def test_zero_fee_keeps_full_price(self):
        approved_card(self.blogger)
        deal = self._paid()
        self.assertTrue(deal.two_fees)
        self.assertEqual((deal.contractor_fee_percent, deal.contractor_fee_withheld), (D("0"), D("0")))
        self.assertEqual(WithdrawalRequest.objects.get(deal=deal).amount, D("100000"))
        self.assertEqual(metrics.platform_revenue(), D("16000"))

    def test_deal_before_rule_has_no_contractor_fee(self):
        deal = self._deal()
        Deal.objects.filter(pk=deal.pk).update(contractor_fee_percent=None, contractor_fee_amount=None)
        deal.refresh_from_db()
        self.assertFalse(deal.two_fees)
        self.assertEqual(deal.blogger_earning, D("100000"))
