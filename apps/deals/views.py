from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response as DRFResponse

from apps.users.models import User
from .models import ChatMessage, Deal, DealStatusLog
from .serializers import ChatMessageSerializer, DealSerializer, DealStatusLogSerializer


def _log_status_change(deal, new_status, user=None, comment=""):
    DealStatusLog.log(deal=deal, new_status=new_status, changed_by=user, comment=comment)
    deal.status = new_status
    deal.save(update_fields=["status", "updated_at"])


class DealViewSet(
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = DealSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == User.Role.BLOGGER:
            return Deal.objects.filter(blogger=user).select_related(
                "campaign", "blogger", "advertiser", "platform"
            )
        if user.role == User.Role.ADVERTISER:
            return Deal.objects.filter(advertiser=user).select_related(
                "campaign", "blogger", "advertiser", "platform"
            )
        return Deal.objects.all().select_related(
            "campaign", "blogger", "advertiser", "platform"
        )

    @action(detail=True, methods=["post"], url_path="submit-creative")
    def submit_creative(self, request, pk=None):
        deal = self.get_object()
        if deal.blogger != request.user:
            raise PermissionDenied("Only the blogger can submit a creative.")
        if deal.status != Deal.Status.IN_PROGRESS:
            return DRFResponse(
                {"detail": "Creative can only be submitted when deal is in progress."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        deal.creative_text = request.data.get("creative_text", deal.creative_text)
        if "creative_media" in request.FILES:
            deal.creative_media = request.FILES["creative_media"]
        deal.creative_submitted_at = timezone.now()
        deal.save(update_fields=["creative_text", "creative_media", "creative_submitted_at"])
        _log_status_change(deal, Deal.Status.ON_APPROVAL, user=request.user)
        return DRFResponse({"detail": "Creative submitted for approval."})

    @action(detail=True, methods=["post"], url_path="approve-creative")
    def approve_creative(self, request, pk=None):
        deal = self.get_object()
        if deal.advertiser != request.user:
            raise PermissionDenied("Only the advertiser can approve a creative.")
        if deal.status != Deal.Status.ON_APPROVAL:
            return DRFResponse(
                {"detail": "Creative is not pending approval."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        deal.creative_approved_at = timezone.now()
        deal.save(update_fields=["creative_approved_at"])
        _log_status_change(deal, Deal.Status.WAITING_PUBLICATION, user=request.user)
        return DRFResponse({"detail": "Creative approved."})

    @action(detail=True, methods=["post"], url_path="reject-creative")
    def reject_creative(self, request, pk=None):
        deal = self.get_object()
        if deal.advertiser != request.user:
            raise PermissionDenied("Only the advertiser can reject a creative.")
        if deal.status != Deal.Status.ON_APPROVAL:
            return DRFResponse(
                {"detail": "Creative is not pending approval."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        reason = request.data.get("reason", "")
        deal.creative_rejection_reason = reason
        deal.save(update_fields=["creative_rejection_reason"])
        _log_status_change(
            deal, Deal.Status.IN_PROGRESS, user=request.user, comment=reason
        )
        return DRFResponse({"detail": "Creative rejected. Blogger should revise and resubmit."})

    @action(detail=True, methods=["post"], url_path="submit-publication")
    def submit_publication(self, request, pk=None):
        deal = self.get_object()
        if deal.blogger != request.user:
            raise PermissionDenied("Only the blogger can submit a publication URL.")
        if deal.status != Deal.Status.WAITING_PUBLICATION:
            return DRFResponse(
                {"detail": "Deal is not in waiting publication status."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        publication_url = request.data.get("publication_url", "")
        if not publication_url:
            raise ValidationError({"publication_url": "Publication URL is required."})
        deal.publication_url = publication_url
        deal.publication_at = timezone.now()
        deal.save(update_fields=["publication_url", "publication_at"])
        _log_status_change(deal, Deal.Status.CHECKING, user=request.user)
        return DRFResponse({"detail": "Publication submitted for checking."})

    @action(detail=True, methods=["post"], url_path="confirm-publication")
    def confirm_publication(self, request, pk=None):
        deal = self.get_object()
        if deal.advertiser != request.user:
            raise PermissionDenied("Only the advertiser can confirm a publication.")
        if deal.status != Deal.Status.CHECKING:
            return DRFResponse(
                {"detail": "Deal is not in checking status."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        _log_status_change(deal, Deal.Status.COMPLETED, user=request.user)
        # Trigger payment
        from apps.billing.services import BillingService
        BillingService.complete_deal_payment(deal)
        return DRFResponse({"detail": "Publication confirmed. Deal completed."})

    @action(detail=True, methods=["post"])
    def dispute(self, request, pk=None):
        deal = self.get_object()
        user = request.user
        if deal.blogger != user and deal.advertiser != user:
            raise PermissionDenied("You are not a participant in this deal.")
        if deal.status not in (Deal.Status.CHECKING, Deal.Status.PUBLISHED):
            return DRFResponse(
                {"detail": "Dispute can only be opened in checking or published status."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        reason = request.data.get("reason", "")
        if not reason:
            raise ValidationError({"reason": "Dispute reason is required."})
        deal.dispute_reason = reason
        deal.dispute_opened_at = timezone.now()
        deal.save(update_fields=["dispute_reason", "dispute_opened_at"])
        _log_status_change(deal, Deal.Status.DISPUTED, user=user, comment=reason)
        return DRFResponse({"detail": "Dispute opened."})

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        deal = self.get_object()
        user = request.user
        if deal.blogger != user and deal.advertiser != user:
            raise PermissionDenied("You are not a participant in this deal.")
        cancellable_statuses = (
            Deal.Status.WAITING_PAYMENT,
            Deal.Status.IN_PROGRESS,
        )
        if deal.status not in cancellable_statuses:
            return DRFResponse(
                {"detail": "Deal cannot be cancelled at this stage."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        _log_status_change(deal, Deal.Status.CANCELLED, user=user)
        # Release reserved funds
        from apps.billing.services import BillingService
        BillingService.release_funds(deal)
        return DRFResponse({"detail": "Deal cancelled."})

    @action(detail=True, methods=["get"], url_path="status-log")
    def status_log(self, request, pk=None):
        deal = self.get_object()
        logs = DealStatusLog.objects.filter(deal=deal)
        serializer = DealStatusLogSerializer(logs, many=True)
        return DRFResponse(serializer.data)


class ChatMessageViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = ChatMessageSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        from django.db.models import Q
        user = self.request.user
        deal_id = self.kwargs.get("deal_id")
        return ChatMessage.objects.filter(
            deal__id=deal_id,
        ).filter(
            Q(deal__blogger=user) | Q(deal__advertiser=user)
        ).select_related("sender").order_by("created_at")
