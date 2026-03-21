from rest_framework import serializers

from .models import Transaction, Wallet, WithdrawalRequest


class WalletSerializer(serializers.ModelSerializer):
    total_balance = serializers.DecimalField(
        max_digits=14, decimal_places=2, read_only=True
    )

    class Meta:
        model = Wallet
        fields = (
            "id",
            "available_balance",
            "reserved_balance",
            "on_withdrawal",
            "total_balance",
            "updated_at",
        )
        read_only_fields = fields


class TransactionSerializer(serializers.ModelSerializer):
    deal_id = serializers.IntegerField(source="deal.id", read_only=True, default=None)

    class Meta:
        model = Transaction
        fields = (
            "id",
            "type",
            "amount",
            "balance_after",
            "deal_id",
            "description",
            "created_at",
        )
        read_only_fields = fields


class WithdrawalRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = WithdrawalRequest
        fields = (
            "id",
            "amount",
            "requisites",
            "status",
            "processed_at",
            "admin_comment",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "status",
            "processed_at",
            "admin_comment",
            "created_at",
            "updated_at",
        )

    def validate_amount(self, value):
        request = self.context["request"]
        try:
            wallet = request.user.wallet
        except Wallet.DoesNotExist:
            raise serializers.ValidationError("Wallet not found.")
        if value > wallet.available_balance:
            raise serializers.ValidationError(
                "Withdrawal amount exceeds available balance."
            )
        return value

    def create(self, validated_data):
        request = self.context["request"]
        return WithdrawalRequest.objects.create(
            blogger=request.user, **validated_data
        )
