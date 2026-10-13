from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response as DRFResponse
from rest_framework.views import APIView

from apps.users.models import User
from .models import Transaction, Wallet, WithdrawalRequest
from .serializers import (
    TransactionSerializer,
    WalletSerializer,
    WithdrawalRequestSerializer,
)


class WalletView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        wallet, _ = Wallet.objects.get_or_create(user=request.user)
        serializer = WalletSerializer(wallet)
        return DRFResponse(serializer.data)


class TransactionListView(generics.ListAPIView):
    serializer_class = TransactionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        try:
            wallet = user.wallet
        except Wallet.DoesNotExist:
            return Transaction.objects.none()
        queryset = Transaction.objects.filter(wallet=wallet)
        tx_type = self.request.query_params.get("type")
        if tx_type:
            queryset = queryset.filter(type=tx_type)
        return queryset


class WithdrawalRequestView(generics.ListAPIView):
    """Выплаты исполнителя — только чтение: с 13.10.2026 выплаты формирует платформа по сделкам."""

    serializer_class = WithdrawalRequestSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role != User.Role.BLOGGER:
            return WithdrawalRequest.objects.none()
        return (
            WithdrawalRequest.objects.filter(blogger=user).select_related("deal")
            .prefetch_related("deal__einvoices")
        )
