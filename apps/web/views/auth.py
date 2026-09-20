from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.users import blocklist, security
from apps.users.models import PasswordResetToken, User
from apps.users.tasks import queue_welcome_email, send_password_reset_email

from ..forms import (
    BloggerIdentitySubmitForm,
    LegalEntityApplicationForm,
    LoginForm,
    PasswordResetConfirmForm,
    PasswordResetRequestForm,
)
from .pages import _redirect_dashboard

# Лимиты защиты от ботов (см. apps/users/security.py). Считаются только
# неудачные попытки входа, чтобы не мешать обычным пользователям за общим NAT.
LOGIN_FAIL_LIMIT, LOGIN_FAIL_WINDOW = 20, 15 * 60          # с одного IP за 15 минут
PASSWORD_RESET_IP_LIMIT, PASSWORD_RESET_WINDOW = 5, 60 * 60  # запросов с IP в час
PASSWORD_RESET_EMAIL_LIMIT = 3                               # писем на один email в час


def login_view(request):
    if request.user.is_authenticated:
        return _redirect_dashboard(request.user)

    form = LoginForm(request.POST or None)
    ip = security.client_ip(request)
    if request.method == "POST" and security.is_limited("login_fail", ip, LOGIN_FAIL_LIMIT):
        blocklist.record_strike(ip, "login_fail")
        form.is_valid()
        form.add_error(None, security.MSG_TOO_MANY)
        return render(request, "auth/login.html", {"form": form}, status=429)

    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].lower()
        password = form.cleaned_data["password"]

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            security.hit("login_fail", ip, LOGIN_FAIL_LIMIT, LOGIN_FAIL_WINDOW)
            form.add_error(None, "Неверный email или пароль.")
            return render(request, "auth/login.html", {"form": form})

        if user.is_blocked:
            form.add_error(None, "Аккаунт заблокирован. Обратитесь в поддержку.")
            return render(request, "auth/login.html", {"form": form})

        if not user.check_password(password):
            security.hit("login_fail", ip, LOGIN_FAIL_LIMIT, LOGIN_FAIL_WINDOW)
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
    """Точка входа в регистрацию — не создаёт аккаунт сама.

    По макету бизнеса (task/bloger 12092026) ни у юрлица, ни у блогера нет
    email/пароля на этом шаге. Страница показывает два таба, каждый со своей
    формой, которая отправляется на свой отдельный обработчик:
    - Рекламодатель → LegalEntityApplicationForm → web:legal_entity_submit
      (название + ИНН, аккаунт появится позже, при выдаче доступа);
    - Блогер → BloggerIdentitySubmitForm → web:blogger_identity_submit
      (ФИО/телефон/ПИНФЛ, подтверждение через OneID).
    """
    if request.user.is_authenticated:
        return _redirect_dashboard(request.user)

    return render(request, "auth/register.html", {
        "legal_entity_form": LegalEntityApplicationForm(),
        "blogger_form": BloggerIdentitySubmitForm(),
        "initial_role": request.GET.get("role", "advertiser"),
    })


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
    queue_welcome_email(user.pk)
    return render(request, "auth/email_confirm_done.html", {"success": True})


def password_reset_request_view(request):
    sent = False
    form = PasswordResetRequestForm(request.POST or None)
    if request.method == "POST":
        problem = security.check_public_form(
            request, "pwreset_ip", PASSWORD_RESET_IP_LIMIT, PASSWORD_RESET_WINDOW,
        )
        if problem == "honeypot":
            # Бот: делаем вид, что письмо ушло, но ничего не отправляем.
            return render(request, "auth/password_reset_request.html", {"form": form, "sent": True})
        if problem:
            form.is_valid()
            form.add_error(None, security.MSG_CAPTCHA if problem == "captcha" else security.MSG_TOO_MANY)
            return render(
                request, "auth/password_reset_request.html",
                {"form": form, "sent": False, "error": form.non_field_errors()[0]},
                status=400 if problem == "captcha" else 429,
            )

    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].lower()
        # Лимит на один email: чужой адрес нельзя завалить письмами. Пользователю
        # отвечаем так же, как при успехе, чтобы не раскрывать, что сработал лимит.
        over_email_limit = security.hit(
            "pwreset_email", email, PASSWORD_RESET_EMAIL_LIMIT, PASSWORD_RESET_WINDOW,
        )
        try:
            user = User.objects.get(email=email)
            if not over_email_limit:
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
