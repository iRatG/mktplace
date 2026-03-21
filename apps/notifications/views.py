from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response as DRFResponse
from rest_framework.views import APIView

from .models import Notification, NotificationSettings
from .serializers import NotificationSerializer, NotificationSettingsSerializer


class NotificationListView(generics.ListAPIView):
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        queryset = Notification.objects.filter(user=user)
        is_read = self.request.query_params.get("is_read")
        if is_read is not None:
            queryset = queryset.filter(is_read=is_read.lower() == "true")
        notification_type = self.request.query_params.get("type")
        if notification_type:
            queryset = queryset.filter(type=notification_type)
        return queryset


class MarkAllReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        updated = Notification.objects.filter(
            user=request.user, is_read=False
        ).update(is_read=True)
        return DRFResponse(
            {"detail": f"Marked {updated} notifications as read."},
            status=status.HTTP_200_OK,
        )


class MarkReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            notification = Notification.objects.get(pk=pk, user=request.user)
        except Notification.DoesNotExist:
            return DRFResponse(
                {"detail": "Notification not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        notification.mark_read()
        return DRFResponse({"detail": "Notification marked as read."})


class NotificationSettingsView(generics.RetrieveUpdateAPIView):
    serializer_class = NotificationSettingsSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "patch"]

    def get_object(self):
        settings_obj, _ = NotificationSettings.objects.get_or_create(
            user=self.request.user,
            defaults={"preferences": {}},
        )
        return settings_obj
