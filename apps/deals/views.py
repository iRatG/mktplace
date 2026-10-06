from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response as DRFResponse

from apps.users.models import User
from . import services as transitions
from .models import ChatMessage, Deal, DealStatusLog
from .services import TransitionError
from .serializers import ChatMessageSerializer, DealSerializer, DealStatusLogSerializer


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

    def _transition(self, func, *args, ok="OK"):
        """Переход сделки из apps/deals/services.py: те же правила, блокировки, журнал и уведомления, что на сайте."""
        deal = self.get_object()
        try:
            func(deal.pk, *args)
        except TransitionError as e:
            return DRFResponse({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return DRFResponse({"detail": ok})

    @action(detail=True, methods=["post"], url_path="submit-creative")
    def submit_creative(self, request, pk=None):
        return self._transition(
            transitions.submit_creative, request.user, request.data.get("creative_text", ""),
            request.FILES.get("creative_media"), ok="Creative submitted for approval.",
        )

    @action(detail=True, methods=["post"], url_path="approve-creative")
    def approve_creative(self, request, pk=None):
        return self._transition(transitions.approve_creative, request.user, ok="Creative approved.")

    @action(detail=True, methods=["post"], url_path="reject-creative")
    def reject_creative(self, request, pk=None):
        return self._transition(
            transitions.reject_creative, request.user, request.data.get("reason", ""),
            ok="Creative rejected. Blogger should revise and resubmit.",
        )

    @action(detail=True, methods=["post"], url_path="submit-publication")
    def submit_publication(self, request, pk=None):
        return self._transition(
            transitions.submit_publication, request.user, request.data.get("publication_url", ""),
            ok="Publication submitted for checking.",
        )

    @action(detail=True, methods=["post"], url_path="confirm-publication")
    def confirm_publication(self, request, pk=None):
        return self._transition(transitions.complete, request.user, ok="Publication confirmed. Deal completed.")

    @action(detail=True, methods=["post"])
    def dispute(self, request, pk=None):
        return self._transition(
            transitions.open_dispute, request.user, request.data.get("reason", ""), ok="Dispute opened.",
        )

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return self._transition(transitions.cancel, request.user, ok="Deal cancelled.")

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
