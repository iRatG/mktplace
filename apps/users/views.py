import uuid
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import EmailConfirmationToken, PasswordResetToken, User
from .serializers import (
    ChangePasswordSerializer,
    LoginSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetSerializer,
    RegisterSerializer,
)
from .tasks import send_confirmation_email, send_password_reset_email


def get_tokens_for_user(user):
    refresh = RefreshToken.for_user(user)
    return {
        "refresh": str(refresh),
        "access": str(refresh.access_token),
    }


class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        token = EmailConfirmationToken.objects.create(
            user=user,
            expires_at=timezone.now() + timezone.timedelta(hours=24),
        )
        send_confirmation_email.delay(user.id, str(token.token))

        return Response(
            {
                "detail": "Registration successful. Please check your email to confirm your account.",
                "email": user.email,
            },
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        tokens = get_tokens_for_user(user)
        return Response(
            {
                "tokens": tokens,
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "role": user.role,
                    "status": user.status,
                },
            },
            status=status.HTTP_200_OK,
        )


class EmailConfirmView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, token):
        try:
            token_obj = EmailConfirmationToken.objects.select_related("user").get(
                token=token
            )
        except EmailConfirmationToken.DoesNotExist:
            return Response(
                {"detail": "Invalid confirmation token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not token_obj.is_valid:
            return Response(
                {"detail": "Token has expired or already been used."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = token_obj.user
        user.is_email_confirmed = True
        user.status = User.Status.ACTIVE
        user.save(update_fields=["is_email_confirmed", "status"])
        token_obj.mark_used()

        tokens = get_tokens_for_user(user)
        return Response(
            {"detail": "Email confirmed successfully.", "tokens": tokens},
            status=status.HTTP_200_OK,
        )


class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PasswordResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        try:
            user = User.objects.get(email=email)
            token = PasswordResetToken.objects.create(
                user=user,
                expires_at=timezone.now() + timezone.timedelta(hours=2),
                ip_address=request.META.get("REMOTE_ADDR"),
            )
            send_password_reset_email.delay(user.id, str(token.token))
        except User.DoesNotExist:
            pass  # Do not reveal whether email exists

        return Response(
            {
                "detail": "If that email address is registered, you will receive a password reset link."
            },
            status=status.HTTP_200_OK,
        )


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token_value = serializer.validated_data["token"]
        new_password = serializer.validated_data["password"]

        try:
            token_obj = PasswordResetToken.objects.select_related("user").get(
                token=token_value
            )
        except PasswordResetToken.DoesNotExist:
            return Response(
                {"detail": "Invalid reset token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not token_obj.is_valid:
            return Response(
                {"detail": "Token has expired or already been used."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = token_obj.user
        user.set_password(new_password)
        user.save(update_fields=["password"])
        token_obj.mark_used()

        return Response(
            {"detail": "Password has been reset successfully."},
            status=status.HTTP_200_OK,
        )


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        user = request.user
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])
        return Response(
            {"detail": "Password changed successfully."},
            status=status.HTTP_200_OK,
        )
