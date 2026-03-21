from django.urls import path
from rest_framework_simplejwt.views import (
    TokenRefreshView,
    TokenVerifyView,
)

from .views import (
    ChangePasswordView,
    EmailConfirmView,
    LoginView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    RegisterView,
)

app_name = "users"

urlpatterns = [
    # Authentication
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", LoginView.as_view(), name="login"),
    path("email-confirm/<uuid:token>/", EmailConfirmView.as_view(), name="email-confirm"),

    # Password management
    path("password-reset/", PasswordResetRequestView.as_view(), name="password-reset"),
    path(
        "password-reset/confirm/",
        PasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
    path("change-password/", ChangePasswordView.as_view(), name="change-password"),

    # JWT token endpoints
    path("token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("token/verify/", TokenVerifyView.as_view(), name="token-verify"),
]
