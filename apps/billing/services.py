from decimal import Decimal

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
            description=f"Payment for completed deal #{deal.pk}",
        )

        # Credit to blogger's available balance
        blogger_wallet.available_balance += amount
        blogger_wallet.save(update_fields=["available_balance", "updated_at"])

        Transaction.objects.create(
            wallet=blogger_wallet,
            type=Transaction.Type.EARNING,
            amount=amount,
            balance_after=blogger_wallet.available_balance,
            deal=deal,
            description=f"Earning for completed deal #{deal.pk}",
        )

        # Update blogger profile stats
        try:
            profile = deal.blogger.blogger_profile
            profile.deals_count += 1
            profile.save(update_fields=["deals_count"])
        except Exception:
            pass

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
