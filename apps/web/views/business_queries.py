"""Views модуля business_queries: внутренние тикеты ИТ-команды + опросники для бизнеса.

ИТ-команда (is_staff + группа "IT Team"):
    /tickets/           — список тикетов
    /tickets/new/       — создать тикет (текст + до 5 скриншотов)
    /tickets/<pk>/      — карточка тикета, смена статуса, связанные опросники

Бизнес-заказчик (без аккаунта, доступ по токену + общий пароль):
    /bq/<token>/        — опросник: гейт по паролю → вопросы A/B → история ответов
"""
import functools
import os

from django.contrib import messages
from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.business_queries.models import (
    BusinessQuery,
    BusinessQuestion,
    BusinessSubmission,
    Ticket,
    TicketAttachment,
    TicketStatusLog,
)

from ..forms import BusinessQueryPasswordForm, TicketForm, TicketStatusForm
from .pages import _redirect_dashboard

MAX_ATTACHMENTS = 5
ALLOWED_ATTACHMENT_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}


def _it_team_required(view_func):
    """Decorator: allow only is_staff users who belong to the "IT Team" group."""
    @functools.wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("web:login")
        if not (request.user.is_staff and request.user.groups.filter(name="IT Team").exists()):
            messages.error(request, "Доступ запрещён.")
            return _redirect_dashboard(request.user)
        return view_func(request, *args, **kwargs)
    _wrapped.__name__ = view_func.__name__
    return _wrapped


def _client_ip(request):
    return (
        request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
        or request.META.get("REMOTE_ADDR")
        or "unknown"
    )


@_it_team_required
def ticket_list(request):
    """Список всех тикетов, видимый всей ИТ-команде."""
    tickets = Ticket.objects.select_related("created_by")
    status = request.GET.get("status")
    if status in Ticket.Status.values:
        tickets = tickets.filter(status=status)
    return render(request, "business_queries/ticket_list.html", {
        "tickets": tickets,
        "statuses": Ticket.Status.choices,
        "active_status": status,
    })


@_it_team_required
def ticket_create(request):
    """Создать тикет: заголовок + описание + до 5 скриншотов/PDF."""
    form = TicketForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        files = request.FILES.getlist("attachments")
        if len(files) > MAX_ATTACHMENTS:
            form.add_error(None, f"Максимум {MAX_ATTACHMENTS} файлов.")
        else:
            bad_files = [
                f.name for f in files
                if os.path.splitext(f.name)[1].lower() not in ALLOWED_ATTACHMENT_EXTENSIONS
            ]
            if bad_files:
                form.add_error(None, f"Недопустимый формат файла: {', '.join(bad_files)}.")
            else:
                ticket = form.save(commit=False)
                ticket.created_by = request.user
                ticket.save()
                for f in files:
                    TicketAttachment.objects.create(ticket=ticket, file=f)
                TicketStatusLog.log(ticket, Ticket.Status.NEW, changed_by=request.user)
                messages.success(request, "Тикет создан.")
                return redirect("web:ticket_detail", pk=ticket.pk)
    return render(request, "business_queries/ticket_form.html", {
        "form": form, "max_attachments": MAX_ATTACHMENTS,
    })


@_it_team_required
def ticket_detail(request, pk):
    """Карточка тикета: описание, вложения, история статусов, связанные опросники."""
    ticket = get_object_or_404(Ticket, pk=pk)
    status_form = TicketStatusForm(initial={"status": ticket.status})

    if request.method == "POST":
        status_form = TicketStatusForm(request.POST)
        if status_form.is_valid():
            new_status = status_form.cleaned_data["status"]
            comment = status_form.cleaned_data["comment"]
            TicketStatusLog.log(ticket, new_status, changed_by=request.user, comment=comment)
            ticket.status = new_status
            ticket.save(update_fields=["status", "updated_at"])
            messages.success(request, "Статус обновлён.")
            return redirect("web:ticket_detail", pk=ticket.pk)

    return render(request, "business_queries/ticket_detail.html", {
        "ticket": ticket,
        "status_form": status_form,
        "attachments": ticket.attachments.all(),
        "status_logs": ticket.status_logs.select_related("changed_by"),
        "queries": ticket.queries.all(),
    })


def business_query_view(request, token):
    """Опросник для бизнеса: гейт по паролю (без аккаунта) → вопросы A/B → история ответов."""
    query = get_object_or_404(BusinessQuery, token=token, is_active=True)
    session_key = f"bq_unlocked_{query.pk}"
    unlocked = request.session.get(session_key, False)
    action = request.POST.get("action") if request.method == "POST" else None

    if not unlocked:
        password_form = BusinessQueryPasswordForm()
        if action == "unlock":
            rate_key = f"bq_login:{_client_ip(request)}:{query.pk}"
            attempts = cache.get(rate_key, 0)
            if attempts >= 8:
                return HttpResponse("Слишком много попыток. Попробуйте позже.", status=429)
            password_form = BusinessQueryPasswordForm(request.POST)
            if password_form.is_valid() and query.check_password(password_form.cleaned_data["password"]):
                request.session[session_key] = True
                unlocked = True
            else:
                cache.set(rate_key, attempts + 1, timeout=3600)
                password_form.add_error(None, "Неверный пароль.")
        if not unlocked:
            return render(request, "business_queries/gate.html", {"query": query, "form": password_form})

    questions = list(query.questions.all())
    context = {"query": query, "questions": questions, "submissions": query.submissions.all()}

    if action == "submit":
        missing_ids, answers = set(), {}
        for q in questions:
            value = request.POST.get(f"q_{q.pk}", "").strip()
            if q.kind == BusinessQuestion.Kind.CHOICE and value not in ("A", "B"):
                missing_ids.add(q.pk)
            else:
                answers[str(q.pk)] = value
        if missing_ids:
            context["missing_ids"] = missing_ids
        else:
            BusinessSubmission.objects.create(
                query=query,
                responder_name=request.POST.get("responder_name", "").strip(),
                comment=request.POST.get("comment", "").strip(),
                answers=answers,
            )
            context["just_submitted"] = True
            context["submissions"] = query.submissions.all()

    return render(request, "business_queries/query.html", context)
