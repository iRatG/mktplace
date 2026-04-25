from rest_framework import generics, status
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
from .services import BillingService


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


class WithdrawalRequestView(generics.ListCreateAPIView):
    serializer_class = WithdrawalRequestSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role != User.Role.BLOGGER:
            return WithdrawalRequest.objects.none()
        return WithdrawalRequest.objects.filter(blogger=user)

    def create(self, request, *args, **kwargs):
        if request.user.role != User.Role.BLOGGER:
            return DRFResponse(
                {"detail": "Only bloggers can request withdrawals."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if request.user.is_demo:
            return DRFResponse(
                {"detail": "Withdrawals are disabled for demo accounts."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        withdrawal = serializer.save()
        BillingService.process_withdrawal(withdrawal)
        return DRFResponse(serializer.data, status=status.HTTP_201_CREATED)
