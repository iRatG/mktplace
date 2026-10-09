from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import models, transaction as db_transaction

from .models import TestBalanceGrant, Transaction, Wallet, WithdrawalRequest


# Сделка, по которой списываются CPA-конверсии: идёт работа или проверка. Завершённая, отменённая
# и спорная сделки конверсий не оплачивают.
CPA_BILLABLE_DEAL_STATUSES = ("in_progress", "on_approval", "waiting_publication", "checking")


class BillingService:
    """Central service for all billing operations."""

    @staticmethod
    def _get_or_create_wallet(user):
        wallet, _ = Wallet.objects.select_for_update().get_or_create(user=user)
        return wallet

    @staticmethod
    def _require_reserved(wallet, amount, deal):
        """Резерв рекламодателя покрывает сделку — иначе учёт уже сломан, и молча «досоздавать» деньги нельзя."""
        if wallet.reserved_balance < amount:
            raise ValueError(
                f"Reserve of {wallet.user_id} ({wallet.reserved_balance}) is less than deal #{deal.pk} amount ({amount})."
            )

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
    def reserve_for_offer(cls, offer):
        """Резерв под индивидуальную оферту при её направлении (ПР 5.2): available → reserved."""
        wallet = cls._get_or_create_wallet(offer.advertiser)
        amount = offer.reserved_amount
        if wallet.available_balance < amount:
            raise ValueError("Insufficient funds to reserve.")
        wallet.available_balance -= amount
        wallet.reserved_balance += amount
        wallet.save(update_fields=["available_balance", "reserved_balance", "updated_at"])
        Transaction.objects.create(
            wallet=wallet, type=Transaction.Type.RESERVE, amount=-amount,
            balance_after=wallet.available_balance, offer=offer,
            description=f"Резерв под оферту #{offer.pk}",
        )
        return wallet

    @classmethod
    @db_transaction.atomic
    def release_offer(cls, offer):
        """Оферта не стала сделкой (отклонена, истекла, кампания завершена) — резерв обратно в доступный."""
        wallet = cls._get_or_create_wallet(offer.advertiser)
        amount = offer.reserved_amount
        if wallet.reserved_balance < amount:
            raise ValueError(f"Reserved balance is less than offer #{offer.pk} amount.")
        wallet.reserved_balance -= amount
        wallet.available_balance += amount
        wallet.save(update_fields=["available_balance", "reserved_balance", "updated_at"])
        Transaction.objects.create(
            wallet=wallet, type=Transaction.Type.RELEASE, amount=amount,
            balance_after=wallet.available_balance, offer=offer,
            description=f"Возврат резерва: оферта #{offer.pk} не принята",
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
        cls._require_reserved(wallet, amount, deal)

        wallet.reserved_balance -= amount
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
        cls._require_reserved(advertiser_wallet, amount, deal)
        advertiser_wallet.reserved_balance -= amount
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
    def complete_withdrawal(cls, withdrawal: WithdrawalRequest, comment=""):
        """Заявка на вывод выплачена: сумма уходит с «на выводе» из системы, пишется транзакция PAYOUT.

        Единственный путь выплаты — панель сотрудника и Django admin вызывают его.
        """
        from django.utils import timezone

        withdrawal = WithdrawalRequest.objects.select_for_update().get(pk=withdrawal.pk)
        if withdrawal.status != WithdrawalRequest.Status.PENDING:
            raise ValueError(f"Withdrawal #{withdrawal.pk} is not pending.")
        wallet = cls._get_or_create_wallet(withdrawal.blogger)
        amount = withdrawal.amount
        if wallet.on_withdrawal < amount:
            raise ValueError(f"On-withdrawal balance is less than withdrawal #{withdrawal.pk} amount.")

        wallet.on_withdrawal -= amount
        wallet.save(update_fields=["on_withdrawal", "updated_at"])
        Transaction.objects.create(
            wallet=wallet,
            type=Transaction.Type.PAYOUT,
            amount=-amount,
            balance_after=wallet.available_balance,
            description=f"Payout for withdrawal request #{withdrawal.pk}",
        )
        withdrawal.status = WithdrawalRequest.Status.COMPLETED
        withdrawal.processed_at = timezone.now()
        withdrawal.admin_comment = comment
        withdrawal.save(update_fields=["status", "processed_at", "admin_comment", "updated_at"])
        return withdrawal

    @classmethod
    @db_transaction.atomic
    def reject_withdrawal(cls, withdrawal: WithdrawalRequest, comment=""):
        """Заявка на вывод отклонена: сумма возвращается на доступный баланс (REFUND)."""
        from django.utils import timezone

        withdrawal = WithdrawalRequest.objects.select_for_update().get(pk=withdrawal.pk)
        if withdrawal.status != WithdrawalRequest.Status.PENDING:
            raise ValueError(f"Withdrawal #{withdrawal.pk} is not pending.")
        cls.refund(withdrawal)
        withdrawal.status = WithdrawalRequest.Status.REJECTED
        withdrawal.processed_at = timezone.now()
        withdrawal.admin_comment = comment
        withdrawal.save(update_fields=["status", "processed_at", "admin_comment", "updated_at"])
        return withdrawal

    @classmethod
    @db_transaction.atomic
    def refund(cls, withdrawal: WithdrawalRequest):
        """
        Refund withdrawal amount back to blogger's available balance
        if the withdrawal request is rejected. Вызывать через reject_withdrawal.
        """
        wallet = cls._get_or_create_wallet(withdrawal.blogger)
        amount = withdrawal.amount
        if wallet.on_withdrawal < amount:
            raise ValueError(f"On-withdrawal balance is less than withdrawal #{withdrawal.pk} amount.")

        wallet.on_withdrawal -= amount
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

    # ── Test Balance (Demo) ──────────────────────────────────────────────────

    @classmethod
    @db_transaction.atomic
    def grant_test_balance(cls, user, amount, granted_by, note=""):
        """
        Credit test balance to a demo account.
        Only allowed for users with is_demo=True.
        Cumulative limit: TestBalanceGrant.MAX_TOTAL per user.
        """
        if not user.is_demo:
            raise ValueError("Test balance can only be granted to demo accounts.")

        amount = Decimal(str(amount))
        if amount <= 0:
            raise ValueError("Amount must be positive.")

        # Сначала блокировка кошелька: параллельные начисления одному пользователю идут по очереди,
        # и сумма уже начисленного ниже читается актуальной.
        wallet = cls._get_or_create_wallet(user)
        already_granted = (
            TestBalanceGrant.objects.filter(user=user)
            .aggregate(total=models.Sum("amount"))["total"]
            or Decimal("0")
        )
        limit = Decimal(str(TestBalanceGrant.MAX_TOTAL))
        if already_granted + amount > limit:
            remaining = limit - already_granted
            raise ValueError(
                f"Limit exceeded. This account can receive at most {remaining} more test credits."
            )

        wallet.available_balance += amount
        wallet.save(update_fields=["available_balance", "updated_at"])

        Transaction.objects.create(
            wallet=wallet,
            type=Transaction.Type.TEST_CREDIT,
            amount=amount,
            balance_after=wallet.available_balance,
            description=f"Test credit granted by admin ({granted_by.email}). {note}".strip(),
        )

        TestBalanceGrant.objects.create(
            user=user,
            amount=amount,
            granted_by=granted_by,
            note=note,
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

        from apps.campaigns.models import Campaign
        from apps.campaigns.validation import budget_remaining
        from apps.deals.models import Deal

        # Статус сделки — свежий и под блокировкой: её могли завершить или отменить параллельно.
        deal = Deal.objects.select_for_update().get(pk=conversion.tracking_link.deal_id)
        amount = conversion.amount
        if deal.status not in CPA_BILLABLE_DEAL_STATUSES:
            raise ValueError(f"Deal #{deal.pk} is not in progress — CPA conversions are not billed.")
        # Все траты кампании (сделки + CPA) не больше её бюджета — блокировка кампании против гонки.
        campaign = Campaign.objects.select_for_update().get(pk=deal.campaign_id)
        if amount > budget_remaining(campaign):
            raise ValueError(f"Campaign #{campaign.pk} budget is exhausted.")

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
