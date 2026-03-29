from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.users.models import PasswordResetToken, User
from apps.users.tasks import send_password_reset_email

from ..forms import (
    LoginForm,
    PasswordResetConfirmForm,
    PasswordResetRequestForm,
    RegisterForm,
)
from .pages import _redirect_dashboard


def login_view(request):
    if request.user.is_authenticated:
        return _redirect_dashboard(request.user)

    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].lower()
        password = form.cleaned_data["password"]

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            form.add_error(None, "Неверный email или пароль.")
            return render(request, "auth/login.html", {"form": form})

        if user.is_blocked:
            form.add_error(None, "Аккаунт заблокирован. Обратитесь в поддержку.")
            return render(request, "auth/login.html", {"form": form})

        if not user.check_password(password):
            user.increment_login_attempts()
            form.add_error(None, "Неверный email или пароль.")
            return render(request, "auth/login.html", {"form": form})

        if not user.is_email_confirmed:
            form.add_error(None, "Подтвердите email перед входом.")
            return render(request, "auth/login.html", {"form": form})

        user.reset_login_attempts()
        login(request, user)
        return _redirect_dashboard(user)

    return render(request, "auth/login.html", {"form": form})


def register_view(request):
    if request.user.is_authenticated:
        return _redirect_dashboard(request.user)

    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = User.objects.create_user(
            email=form.cleaned_data["email"],
            password=form.cleaned_data["password1"],
            role=form.cleaned_data["role"],
        )
        # Send confirmation email via Celery
        from apps.users.tasks import send_confirmation_email
        send_confirmation_email.delay(user.pk)
        messages.success(
            request,
            "Аккаунт создан! Проверьте почту и подтвердите email.",
        )
        return redirect("web:login")

    return render(request, "auth/register.html", {"form": form})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("web:login")


def email_confirm_view(request, token):
    from apps.users.models import EmailConfirmationToken as ECToken
    try:
        tok = ECToken.objects.get(token=token)
    except ECToken.DoesNotExist:
        return render(request, "auth/email_confirm_done.html", {"success": False})

    if not tok.is_valid:
        return render(request, "auth/email_confirm_done.html", {"success": False})

    tok.mark_used()
    user = tok.user
    user.is_email_confirmed = True
    user.status = User.Status.ACTIVE
    user.save(update_fields=["is_email_confirmed", "status"])
    return render(request, "auth/email_confirm_done.html", {"success": True})


def password_reset_request_view(request):
    sent = False
    form = PasswordResetRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].lower()
        try:
            user = User.objects.get(email=email)
            send_password_reset_email.delay(user.pk)
        except User.DoesNotExist:
            pass  # Don't reveal if user exists
        sent = True

    return render(request, "auth/password_reset_request.html", {"form": form, "sent": sent})


def password_reset_confirm_view(request, token):
    try:
        tok = PasswordResetToken.objects.get(token=token)
    except PasswordResetToken.DoesNotExist:
        return render(request, "auth/password_reset_confirm.html", {"invalid_token": True})

    if not tok.is_valid:
        return render(request, "auth/password_reset_confirm.html", {"invalid_token": True})

    form = PasswordResetConfirmForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        tok.user.set_password(form.cleaned_data["password1"])
        tok.user.save(update_fields=["password"])
        tok.mark_used()
        messages.success(request, "Пароль изменён. Войдите с новым паролем.")
        return redirect("web:login")

    return render(request, "auth/password_reset_confirm.html", {"form": form, "invalid_token": False})
