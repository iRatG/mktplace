from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import transaction as db_transaction

from .models import Transaction, Wallet, WithdrawalRequest


class BillingService:
    """Central service for all billing operations."""

    @staticmethod
    def _get_or_create_wallet(user):
        wallet, _ = Wallet.objects.select_for_update().get_or_create(user=user)
        return wallet

    @classmethod
    @db_transaction.atomic
    def reserve_funds(cls, deal):
        """
        Reserve funds from advertiser's wallet when a deal is created.
        Moves amount from available_balance to reserved_balance.
        """
        wallet = cls._get_or_create_wallet(deal.advertiser)
        amount = deal.amount

        if wallet.available_balance < amount:
            raise ValueError("Insufficient funds to reserve.")

        wallet.available_balance -= amount
        wallet.reserved_balance += amount
        wallet.save(update_fields=["available_balance", "reserved_balance", "updated_at"])

        Transaction.objects.create(
            wallet=wallet,
            type=Transaction.Type.RESERVE,
            amount=-amount,
            balance_after=wallet.available_balance,
            deal=deal,
            description=f"Reserved for deal #{deal.pk}",
        )
        return wallet

    @classmethod
    @db_transaction.atomic
    def release_funds(cls, deal):
        """
        Release reserved funds back to advertiser's available balance.
        Used when a deal is cancelled.
        """
        wallet = cls._get_or_create_wallet(deal.advertiser)
        amount = deal.amount

        wallet.reserved_balance = max(Decimal("0"), wallet.reserved_balance - amount)
        wallet.available_balance += amount
        wallet.save(update_fields=["available_balance", "reserved_balance", "updated_at"])

        Transaction.objects.create(
            wallet=wallet,
            type=Transaction.Type.RELEASE,
            amount=amount,
            balance_after=wallet.available_balance,
            deal=deal,
            description=f"Released reservation for deal #{deal.pk}",
        )
        return wallet

    @classmethod
    @db_transaction.atomic
    def complete_deal_payment(cls, deal):
        """
        Transfer payment from advertiser's reserved balance to blogger's available balance.
        Called when a deal is completed.
        """
        advertiser_wallet = cls._get_or_create_wallet(deal.advertiser)
        blogger_wallet = cls._get_or_create_wallet(deal.blogger)
        amount = deal.amount

        commission_percent = Decimal(
            getattr(settings, "PLATFORM_COMMISSION_PERCENT", 15)
        )
        commission = (amount * commission_percent / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        blogger_earning = amount - commission

        # Deduct from advertiser's reserved balance
        advertiser_wallet.reserved_balance = max(
            Decimal("0"), advertiser_wallet.reserved_balance - amount
        )
        advertiser_wallet.save(update_fields=["reserved_balance", "updated_at"])

        Transaction.objects.create(
            wallet=advertiser_wallet,
            type=Transaction.Type.PAYMENT,
            amount=-amount,
            balance_after=advertiser_wallet.available_balance,
            deal=deal,
            description=f"Payment for completed deal #{deal.pk} (commission {commission_percent}%)",
        )

        # Credit blogger's earning (amount minus platform commission)
        blogger_wallet.available_balance += blogger_earning
        blogger_wallet.save(update_fields=["available_balance", "updated_at"])

        Transaction.objects.create(
            wallet=blogger_wallet,
            type=Transaction.Type.EARNING,
            amount=blogger_earning,
            balance_after=blogger_wallet.available_balance,
            deal=deal,
            description=f"Earning for completed deal #{deal.pk} (after {commission_percent}% commission)",
        )

        # Update blogger profile stats
        profile = getattr(deal.blogger, "blogger_profile", None)
        if profile is not None:
            profile.deals_count += 1
            profile.save(update_fields=["deals_count"])

        return advertiser_wallet, blogger_wallet

    @classmethod
    @db_transaction.atomic
    def process_withdrawal(cls, withdrawal: WithdrawalRequest):
        """
        Move funds from blogger's available_balance to on_withdrawal
        when a withdrawal request is created.
        """
        wallet = cls._get_or_create_wallet(withdrawal.blogger)
        amount = withdrawal.amount

        if wallet.available_balance < amount:
            raise ValueError("Insufficient funds for withdrawal.")

        wallet.available_balance -= amount
        wallet.on_withdrawal += amount
        wallet.save(update_fields=["available_balance", "on_withdrawal", "updated_at"])

        Transaction.objects.create(
            wallet=wallet,
            type=Transaction.Type.WITHDRAWAL,
            amount=-amount,
            balance_after=wallet.available_balance,
            description=f"Withdrawal request #{withdrawal.pk}",
        )
        return wallet

    @classmethod
    @db_transaction.atomic
    def refund(cls, withdrawal: WithdrawalRequest):
        """
        Refund withdrawal amount back to blogger's available balance
        if the withdrawal request is rejected.
        """
        wallet = cls._get_or_create_wallet(withdrawal.blogger)
        amount = withdrawal.amount

        wallet.on_withdrawal = max(Decimal("0"), wallet.on_withdrawal - amount)
        wallet.available_balance += amount
        wallet.save(update_fields=["available_balance", "on_withdrawal", "updated_at"])

        Transaction.objects.create(
            wallet=wallet,
            type=Transaction.Type.REFUND,
            amount=amount,
            balance_after=wallet.available_balance,
            description=f"Refund for rejected withdrawal request #{withdrawal.pk}",
        )
        return wallet

    # ── CPA (Sprint 8) ──────────────────────────────────────────────────────

    @classmethod
    @db_transaction.atomic
    def credit_cpa_conversion(cls, conversion):
        """
        Credit blogger for a CPA conversion.

        - Deducts `conversion.amount` from advertiser's available_balance.
        - Credits blogger after platform commission.
        - Marks conversion.credited = True.
        - Idempotent: raises ValueError if already credited.
        """
        from apps.deals.models import Conversion  # local import to avoid circular

        if conversion.credited:
            raise ValueError(f"Conversion #{conversion.pk} is already credited.")

        deal = conversion.tracking_link.deal
        amount = conversion.amount

        advertiser_wallet = cls._get_or_create_wallet(deal.advertiser)
        if advertiser_wallet.available_balance < amount:
            raise ValueError("Advertiser has insufficient funds for CPA conversion.")

        commission_percent = Decimal(
            getattr(settings, "PLATFORM_COMMISSION_PERCENT", 15)
        )
        commission = (amount * commission_percent / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        blogger_earning = amount - commission

        # Deduct from advertiser
        advertiser_wallet.available_balance -= amount
        advertiser_wallet.save(update_fields=["available_balance", "updated_at"])
        Transaction.objects.create(
            wallet=advertiser_wallet,
            type=Transaction.Type.PAYMENT,
            amount=-amount,
            balance_after=advertiser_wallet.available_balance,
            deal=deal,
            description=f"CPA conversion #{conversion.pk} for deal #{deal.pk}",
        )

        # Credit blogger
        blogger_wallet = cls._get_or_create_wallet(deal.blogger)
        blogger_wallet.available_balance += blogger_earning
        blogger_wallet.save(update_fields=["available_balance", "updated_at"])
        Transaction.objects.create(
            wallet=blogger_wallet,
            type=Transaction.Type.EARNING,
            amount=blogger_earning,
            balance_after=blogger_wallet.available_balance,
            deal=deal,
            description=f"CPA earning for conversion #{conversion.pk} deal #{deal.pk}",
        )

        conversion.credited = True
        conversion.save(update_fields=["credited"])
        return advertiser_wallet, blogger_wallet
