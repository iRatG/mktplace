import uuid
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.utils import timezone


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("status", User.Status.ACTIVE)
        extra_fields.setdefault("is_email_confirmed", True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        ADVERTISER = "advertiser", "Advertiser"
        BLOGGER = "blogger", "Blogger"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        BLOCKED = "blocked", "Blocked"
        DELETED = "deleted", "Deleted"

    email = models.EmailField(unique=True, db_index=True)
    role = models.CharField(max_length=20, choices=Role.choices)
    is_email_confirmed = models.BooleanField(default=False)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    email_confirmation_token = models.UUIDField(null=True, blank=True, default=None)
    email_confirmation_expires = models.DateTimeField(null=True, blank=True)
    login_attempts = models.PositiveSmallIntegerField(default=0)
    blocked_until = models.DateTimeField(null=True, blank=True)

    is_demo = models.BooleanField(default=False, help_text="Demo account — test balance only, withdrawals blocked")
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    date_joined = models.DateTimeField(default=timezone.now)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"

    def __str__(self):
        return f"{self.email} ({self.role})"

    @property
    def is_blocked(self):
        if self.status == self.Status.BLOCKED:
            return True
        if self.blocked_until and self.blocked_until > timezone.now():
            return True
        return False

    def increment_login_attempts(self):
        self.login_attempts += 1
        if self.login_attempts >= 5:
            self.blocked_until = timezone.now() + timezone.timedelta(minutes=15)
        self.save(update_fields=["login_attempts", "blocked_until"])

    def reset_login_attempts(self):
        self.login_attempts = 0
        self.blocked_until = None
        self.save(update_fields=["login_attempts", "blocked_until"])


class EmailConfirmationToken(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="email_confirmation_tokens",
    )
    token = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Email Confirmation Token"
        verbose_name_plural = "Email Confirmation Tokens"

    def __str__(self):
        return f"EmailConfirmationToken for {self.user.email}"

    @property
    def is_valid(self):
        return not self.is_used and self.expires_at > timezone.now()

    def mark_used(self):
        self.is_used = True
        self.save(update_fields=["is_used"])


class PasswordResetToken(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="password_reset_tokens",
    )
    token = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        verbose_name = "Password Reset Token"
        verbose_name_plural = "Password Reset Tokens"

    def __str__(self):
        return f"PasswordResetToken for {self.user.email}"

    @property
    def is_valid(self):
        return not self.is_used and self.expires_at > timezone.now()

    def mark_used(self):
        self.is_used = True
        self.save(update_fields=["is_used"])
