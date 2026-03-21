from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from .models import User


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True, required=True, validators=[validate_password]
    )
    password_confirm = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = User
        fields = ("email", "role", "password", "password_confirm")

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError(
                {"password_confirm": _("Passwords do not match.")}
            )
        return attrs

    def validate_role(self, value):
        if value not in (User.Role.ADVERTISER, User.Role.BLOGGER):
            raise serializers.ValidationError(_("Invalid role."))
        return value

    def create(self, validated_data):
        validated_data.pop("password_confirm")
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    password = serializers.CharField(required=True, write_only=True)

    def validate(self, attrs):
        email = attrs.get("email")
        password = attrs.get("password")

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise serializers.ValidationError(_("Invalid credentials."))

        if user.is_blocked:
            raise serializers.ValidationError(
                _("Account is blocked. Please try again later.")
            )

        authenticated = authenticate(
            request=self.context.get("request"),
            username=email,
            password=password,
        )
        if not authenticated:
            user.increment_login_attempts()
            raise serializers.ValidationError(_("Invalid credentials."))

        if not user.is_email_confirmed:
            raise serializers.ValidationError(
                _("Please confirm your email before logging in.")
            )

        if user.status != User.Status.ACTIVE:
            raise serializers.ValidationError(
                _("Your account is not active.")
            )

        user.reset_login_attempts()
        attrs["user"] = authenticated
        return attrs


class PasswordResetSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)

    def validate_email(self, value):
        if not User.objects.filter(email=value).exists():
            # Do not reveal whether the email exists
            return value
        return value


class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.UUIDField(required=True)
    password = serializers.CharField(
        write_only=True, required=True, validators=[validate_password]
    )
    password_confirm = serializers.CharField(write_only=True, required=True)

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError(
                {"password_confirm": _("Passwords do not match.")}
            )
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(required=True, write_only=True)
    new_password = serializers.CharField(
        write_only=True, required=True, validators=[validate_password]
    )
    new_password_confirm = serializers.CharField(write_only=True, required=True)

    def validate_old_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError(_("Old password is incorrect."))
        return value

    def validate(self, attrs):
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError(
                {"new_password_confirm": _("Passwords do not match.")}
            )
        return attrs
