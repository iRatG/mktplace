from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response as DRFResponse

from apps.notifications.service import NotificationService
from apps.users.models import User
from .models import Campaign
from .models import Response as CampaignResponse
from .serializers import CampaignCreateSerializer, CampaignSerializer, ResponseSerializer
from .services import AcceptError, accept_response, expired_error
from .validation import EDITABLE_STATUSES


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
        if instance.status not in EDITABLE_STATUSES:
            raise PermissionDenied("Only draft, rejected or paused campaigns can be edited.")
        campaign = serializer.save()
        # Правка приостановленной кампании — только через повторную модерацию.
        if instance.status == Campaign.Status.PAUSED:
            campaign.status = Campaign.Status.MODERATION
            campaign.save(update_fields=["status"])
            NotificationService.notify_campaign_moderation_requested(
                campaign, NotificationService.MODERATION_AFTER_PAUSE_EDIT
            )

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
        if campaign.status not in (Campaign.Status.DRAFT, Campaign.Status.REJECTED):
            return DRFResponse(
                {"detail": "Only draft or rejected campaigns can be submitted for moderation."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        source = (
            NotificationService.MODERATION_AFTER_REJECTION
            if campaign.status == Campaign.Status.REJECTED
            else NotificationService.MODERATION_FIRST
        )
        campaign.status = Campaign.Status.MODERATION
        campaign.save(update_fields=["status"])
        NotificationService.notify_campaign_moderation_requested(campaign, source)
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
        if expired_error(campaign):
            return DRFResponse({"detail": expired_error(campaign)}, status=status.HTTP_400_BAD_REQUEST)
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

    def perform_create(self, serializer):
        response_obj = serializer.save()
        NotificationService.notify_new_response(
            response_obj.campaign.advertiser, response_obj.campaign, response_obj.blogger
        )

    def perform_destroy(self, instance):
        if instance.blogger != self.request.user:
            raise PermissionDenied("You can only withdraw your own responses.")
        if instance.status != CampaignResponse.Status.PENDING:
            raise PermissionDenied("Only pending responses can be withdrawn.")
        instance.status = CampaignResponse.Status.WITHDRAWN
        instance.save(update_fields=["status"])

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        """Принятие отклика — apps/campaigns/services.accept_response (одна логика с сайтом)."""
        response_obj = self.get_object()
        if response_obj.campaign.advertiser != request.user:
            raise PermissionDenied("Only the campaign advertiser can accept responses.")
        try:
            deal = accept_response(response_obj.pk, request.user)
        except AcceptError as e:
            return DRFResponse({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
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
        response_obj.rejection_reason = str(request.data.get("reason", "")).strip()
        response_obj.save(update_fields=["status", "rejection_reason", "updated_at"])
        NotificationService.notify_response_rejected(
            response_obj.blogger, response_obj.campaign, response_obj.rejection_reason
        )
        return DRFResponse({"detail": "Response rejected."})
