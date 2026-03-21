from django.db import transaction as db_transaction

from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response as DRFResponse

from apps.billing.services import BillingService
from apps.deals.models import Deal, DealStatusLog
from apps.users.models import User
from .models import Campaign
from .models import Response as CampaignResponse
from .serializers import CampaignCreateSerializer, CampaignSerializer, ResponseSerializer


class CampaignViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == User.Role.ADVERTISER:
            return Campaign.objects.filter(advertiser=user).select_related(
                "advertiser", "category"
            )
        # Bloggers see active campaigns
        return Campaign.objects.filter(
            status=Campaign.Status.ACTIVE
        ).select_related("advertiser", "category")

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return CampaignCreateSerializer
        return CampaignSerializer

    def perform_create(self, serializer):
        if self.request.user.role != User.Role.ADVERTISER:
            raise PermissionDenied("Only advertisers can create campaigns.")
        serializer.save()

    def perform_update(self, serializer):
        instance = self.get_object()
        if instance.advertiser != self.request.user:
            raise PermissionDenied("You can only edit your own campaigns.")
        if instance.status not in (Campaign.Status.DRAFT, Campaign.Status.REJECTED):
            raise PermissionDenied("Only draft or rejected campaigns can be edited.")
        serializer.save()

    def perform_destroy(self, instance):
        if instance.advertiser != self.request.user:
            raise PermissionDenied("You can only delete your own campaigns.")
        if instance.status not in (Campaign.Status.DRAFT, Campaign.Status.CANCELLED):
            raise PermissionDenied("Only draft or cancelled campaigns can be deleted.")
        instance.delete()

    @action(detail=True, methods=["post"])
    def submit_for_moderation(self, request, pk=None):
        campaign = self.get_object()
        if campaign.advertiser != request.user:
            raise PermissionDenied("You can only submit your own campaigns.")
        if campaign.status != Campaign.Status.DRAFT:
            return DRFResponse(
                {"detail": "Only draft campaigns can be submitted for moderation."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        campaign.status = Campaign.Status.MODERATION
        campaign.save(update_fields=["status"])
        return DRFResponse({"detail": "Campaign submitted for moderation."})

    @action(detail=True, methods=["post"])
    def pause(self, request, pk=None):
        campaign = self.get_object()
        if campaign.advertiser != request.user:
            raise PermissionDenied()
        if campaign.status != Campaign.Status.ACTIVE:
            return DRFResponse(
                {"detail": "Only active campaigns can be paused."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        campaign.status = Campaign.Status.PAUSED
        campaign.save(update_fields=["status"])
        return DRFResponse({"detail": "Campaign paused."})

    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None):
        campaign = self.get_object()
        if campaign.advertiser != request.user:
            raise PermissionDenied()
        if campaign.status != Campaign.Status.PAUSED:
            return DRFResponse(
                {"detail": "Only paused campaigns can be resumed."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        campaign.status = Campaign.Status.ACTIVE
        campaign.save(update_fields=["status"])
        return DRFResponse({"detail": "Campaign resumed."})

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        campaign = self.get_object()
        if campaign.advertiser != request.user:
            raise PermissionDenied()
        if campaign.status in (Campaign.Status.COMPLETED, Campaign.Status.CANCELLED):
            return DRFResponse(
                {"detail": "Campaign is already completed or cancelled."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        campaign.status = Campaign.Status.CANCELLED
        campaign.save(update_fields=["status"])
        return DRFResponse({"detail": "Campaign cancelled."})


class ResponseViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = ResponseSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == User.Role.BLOGGER:
            return CampaignResponse.objects.filter(
                blogger=user
            ).select_related("blogger", "campaign", "platform")
        if user.role == User.Role.ADVERTISER:
            return CampaignResponse.objects.filter(
                campaign__advertiser=user
            ).select_related("blogger", "campaign", "platform")
        return CampaignResponse.objects.none()

    def perform_destroy(self, instance):
        if instance.blogger != self.request.user:
            raise PermissionDenied("You can only withdraw your own responses.")
        if instance.status != CampaignResponse.Status.PENDING:
            raise PermissionDenied("Only pending responses can be withdrawn.")
        instance.status = CampaignResponse.Status.WITHDRAWN
        instance.save(update_fields=["status"])

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        response_obj = self.get_object()
        if response_obj.campaign.advertiser != request.user:
            raise PermissionDenied("Only the campaign advertiser can accept responses.")
        if response_obj.status != CampaignResponse.Status.PENDING:
            return DRFResponse(
                {"detail": "Only pending responses can be accepted."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        campaign = response_obj.campaign

        # Проверяем статус кампании
        if campaign.status != Campaign.Status.ACTIVE:
            return DRFResponse(
                {"detail": "Cannot accept responses for a non-active campaign."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Проверяем лимит блогеров
        if campaign.max_bloggers > 0:
            active_deals_count = Deal.objects.filter(
                campaign=campaign,
                status__in=[
                    Deal.Status.IN_PROGRESS,
                    Deal.Status.CHECKING,
                    Deal.Status.ON_APPROVAL,
                    Deal.Status.WAITING_PUBLICATION,
                    Deal.Status.COMPLETED,
                ],
            ).count()
            if active_deals_count >= campaign.max_bloggers:
                return DRFResponse(
                    {"detail": f"Campaign has reached the maximum number of bloggers ({campaign.max_bloggers})."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        amount = response_obj.proposed_price or campaign.fixed_price
        if not amount:
            return DRFResponse(
                {"detail": "Cannot determine deal amount: no price agreed upon."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with db_transaction.atomic():
                response_obj.status = CampaignResponse.Status.ACCEPTED
                response_obj.save(update_fields=["status"])

                deal = Deal.objects.create(
                    campaign=campaign,
                    blogger=response_obj.blogger,
                    platform=response_obj.platform,
                    advertiser=request.user,
                    response=response_obj,
                    amount=amount,
                    status=Deal.Status.WAITING_PAYMENT,
                )

                BillingService.reserve_funds(deal)

                deal.status = Deal.Status.IN_PROGRESS
                deal.save(update_fields=["status"])
                DealStatusLog.log(
                    deal,
                    Deal.Status.IN_PROGRESS,
                    changed_by=request.user,
                    comment="Deal created, funds reserved.",
                )
        except ValueError as e:
            return DRFResponse({"detail": str(e)}, status=status.HTTP_402_PAYMENT_REQUIRED)

        return DRFResponse(
            {"detail": "Response accepted. Deal created.", "deal_id": deal.pk},
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        response_obj = self.get_object()
        if response_obj.campaign.advertiser != request.user:
            raise PermissionDenied("Only the campaign advertiser can reject responses.")
        if response_obj.status != CampaignResponse.Status.PENDING:
            return DRFResponse(
                {"detail": "Only pending responses can be rejected."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        response_obj.status = CampaignResponse.Status.REJECTED
        response_obj.save(update_fields=["status"])
        return DRFResponse({"detail": "Response rejected."})
