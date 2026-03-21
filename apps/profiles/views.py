from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.models import User
from .models import AdvertiserProfile, BloggerProfile
from .serializers import (
    AdvertiserProfileSerializer,
    BloggerProfileSerializer,
    PublicBloggerProfileSerializer,
)


class AdvertiserProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = AdvertiserProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        profile, _ = AdvertiserProfile.objects.get_or_create(user=self.request.user)
        return profile

    def get(self, request, *args, **kwargs):
        if request.user.role != User.Role.ADVERTISER:
            return Response(
                {"detail": "Only advertisers have this profile."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().get(request, *args, **kwargs)

    def patch(self, request, *args, **kwargs):
        if request.user.role != User.Role.ADVERTISER:
            return Response(
                {"detail": "Only advertisers can update this profile."},
                status=status.HTTP_403_FORBIDDEN,
            )
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)


class BloggerProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = BloggerProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        profile, _ = BloggerProfile.objects.get_or_create(user=self.request.user)
        return profile

    def get(self, request, *args, **kwargs):
        if request.user.role != User.Role.BLOGGER:
            return Response(
                {"detail": "Only bloggers have this profile."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().get(request, *args, **kwargs)

    def patch(self, request, *args, **kwargs):
        if request.user.role != User.Role.BLOGGER:
            return Response(
                {"detail": "Only bloggers can update this profile."},
                status=status.HTTP_403_FORBIDDEN,
            )
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)


class PublicBloggerProfileView(generics.RetrieveAPIView):
    serializer_class = PublicBloggerProfileSerializer
    permission_classes = [IsAuthenticated]
    queryset = BloggerProfile.objects.select_related("user").all()

    def get_object(self):
        user_id = self.kwargs.get("user_id")
        return get_object_or_404(
            BloggerProfile.objects.select_related("user"),
            user__id=user_id,
            user__role=User.Role.BLOGGER,
        )
