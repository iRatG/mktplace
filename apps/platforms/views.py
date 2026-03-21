from rest_framework import generics, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.models import User
from .models import Category, Platform
from .serializers import CategorySerializer, PlatformCreateSerializer, PlatformSerializer


class PlatformViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == User.Role.BLOGGER:
            return Platform.objects.filter(blogger=user).prefetch_related("categories")
        # Advertisers can see approved platforms only
        return Platform.objects.filter(
            status=Platform.Status.APPROVED
        ).prefetch_related("categories")

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return PlatformCreateSerializer
        return PlatformSerializer

    def perform_create(self, serializer):
        if self.request.user.role != User.Role.BLOGGER:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only bloggers can add platforms.")
        serializer.save()

    def perform_update(self, serializer):
        instance = self.get_object()
        if instance.blogger != self.request.user:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("You can only edit your own platforms.")
        # Re-send for moderation on update
        serializer.save(status=Platform.Status.PENDING)

    def perform_destroy(self, instance):
        if instance.blogger != self.request.user:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("You can only delete your own platforms.")
        instance.delete()


class CategoryListView(generics.ListAPIView):
    serializer_class = CategorySerializer
    permission_classes = [IsAuthenticated]
    queryset = Category.objects.all()
