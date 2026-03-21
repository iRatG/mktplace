from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.models import Transaction, Wallet, WithdrawalRequest
from apps.campaigns.models import Campaign
from apps.campaigns.models import Response as CampaignResponse
from apps.deals.models import Deal
from apps.platforms.models import Platform
from apps.users.models import User
from .serializers import (
    AdminDashboardSerializer,
    AdvertiserDashboardSerializer,
    BloggerDashboardSerializer,
)


class AdvertiserDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if user.role != User.Role.ADVERTISER:
            return Response({"detail": "Only advertisers can access this dashboard."}, status=403)

        campaigns = Campaign.objects.filter(advertiser=user)
        deals = Deal.objects.filter(advertiser=user)

        campaigns_by_status = dict(
            campaigns.values("status").annotate(count=Count("id")).values_list("status", "count")
        )
        deals_by_status = dict(
            deals.values("status").annotate(count=Count("id")).values_list("status", "count")
        )

        total_spent = (
            Transaction.objects.filter(
                wallet__user=user,
                type=Transaction.Type.PAYMENT,
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        )

        data = {
            "total_campaigns": campaigns.count(),
            "active_campaigns": campaigns.filter(status=Campaign.Status.ACTIVE).count(),
            "total_deals": deals.count(),
            "completed_deals": deals.filter(status=Deal.Status.COMPLETED).count(),
            "total_spent": abs(total_spent),
            "active_deals": deals.filter(
                status__in=[Deal.Status.IN_PROGRESS, Deal.Status.ON_APPROVAL,
                            Deal.Status.WAITING_PUBLICATION, Deal.Status.CHECKING]
            ).count(),
            "pending_responses": CampaignResponse.objects.filter(
                campaign__advertiser=user,
                status=CampaignResponse.Status.PENDING,
            ).count(),
            "campaigns_by_status": campaigns_by_status,
            "deals_by_status": deals_by_status,
        }

        serializer = AdvertiserDashboardSerializer(data)
        return Response(serializer.data)


class BloggerDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if user.role != User.Role.BLOGGER:
            return Response({"detail": "Only bloggers can access this dashboard."}, status=403)

        deals = Deal.objects.filter(blogger=user)
        deals_by_status = dict(
            deals.values("status").annotate(count=Count("id")).values_list("status", "count")
        )

        total_earned = (
            Transaction.objects.filter(
                wallet__user=user,
                type=Transaction.Type.EARNING,
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        )

        try:
            available_balance = user.wallet.available_balance
        except Wallet.DoesNotExist:
            available_balance = Decimal("0")

        try:
            rating = user.blogger_profile.rating
        except Exception:
            rating = Decimal("0")

        platforms = Platform.objects.filter(blogger=user)

        data = {
            "total_deals": deals.count(),
            "completed_deals": deals.filter(status=Deal.Status.COMPLETED).count(),
            "active_deals": deals.filter(
                status__in=[Deal.Status.IN_PROGRESS, Deal.Status.ON_APPROVAL,
                            Deal.Status.WAITING_PUBLICATION, Deal.Status.CHECKING]
            ).count(),
            "total_earned": total_earned,
            "available_balance": available_balance,
            "rating": rating,
            "total_platforms": platforms.count(),
            "approved_platforms": platforms.filter(status=Platform.Status.APPROVED).count(),
            "pending_responses": CampaignResponse.objects.filter(
                blogger=user, status=CampaignResponse.Status.PENDING
            ).count(),
            "deals_by_status": deals_by_status,
        }

        serializer = BloggerDashboardSerializer(data)
        return Response(serializer.data)


class AdminDashboardView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        today = timezone.now().date()

        pending_withdrawals_agg = WithdrawalRequest.objects.filter(
            status=WithdrawalRequest.Status.PENDING
        ).aggregate(
            count=Count("id"),
            total=Sum("amount"),
        )

        total_volume = (
            Transaction.objects.filter(
                type=Transaction.Type.PAYMENT
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        )

        data = {
            "total_users": User.objects.count(),
            "total_advertisers": User.objects.filter(role=User.Role.ADVERTISER).count(),
            "total_bloggers": User.objects.filter(role=User.Role.BLOGGER).count(),
            "new_users_today": User.objects.filter(date_joined__date=today).count(),
            "total_campaigns": Campaign.objects.count(),
            "active_campaigns": Campaign.objects.filter(status=Campaign.Status.ACTIVE).count(),
            "campaigns_pending_moderation": Campaign.objects.filter(
                status=Campaign.Status.MODERATION
            ).count(),
            "total_deals": Deal.objects.count(),
            "active_deals": Deal.objects.filter(
                status__in=[Deal.Status.IN_PROGRESS, Deal.Status.ON_APPROVAL,
                            Deal.Status.WAITING_PUBLICATION, Deal.Status.CHECKING]
            ).count(),
            "disputed_deals": Deal.objects.filter(status=Deal.Status.DISPUTED).count(),
            "total_platforms": Platform.objects.count(),
            "platforms_pending_moderation": Platform.objects.filter(
                status=Platform.Status.PENDING
            ).count(),
            "total_volume": abs(total_volume),
            "pending_withdrawals": pending_withdrawals_agg["count"] or 0,
            "pending_withdrawals_amount": pending_withdrawals_agg["total"] or Decimal("0"),
        }

        serializer = AdminDashboardSerializer(data)
        return Response(serializer.data)
