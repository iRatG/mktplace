from django.urls import path

from .views import TransactionListView, WalletView, WithdrawalRequestView

app_name = "billing"

urlpatterns = [
    path("wallet/", WalletView.as_view(), name="wallet"),
    path("transactions/", TransactionListView.as_view(), name="transaction-list"),
    path("withdrawals/", WithdrawalRequestView.as_view(), name="withdrawal-list"),
]
