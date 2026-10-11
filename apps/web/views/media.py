"""Раздача загруженных файлов (/media/…) с проверкой прав — вместо открытой раздачи каталога media.

Файл открывается, только если он записан в своей модели и пользователь имеет к нему доступ; иначе — 404, без
подсказки, что файл существует. Сотрудник видит всё; если он открывает чужой документ, это пишется в журнал доступа
к персональным данным (apps.users.pd_access).
"""
import mimetypes
import posixpath

from django.core.files.storage import default_storage
from django.http import FileResponse, Http404

from apps.users.pd_access import log_access


def _deal_file(model_path, field, label, deal_of, related):
    """Правило для файлов сделки: открывают стороны сделки; в журнал — данные блогера."""
    def rule(path):
        from django.apps import apps

        obj = apps.get_model(*model_path.split(".")).objects.filter(**{field: path}).select_related(*related).first()
        if obj is None:
            return None
        deal = deal_of(obj)
        return {deal.advertiser_id, deal.blogger_id}, deal.blogger, f"{label}, сделка #{deal.pk}"
    return rule


def _owned_file(model_path, label):
    def rule(path):
        from django.apps import apps

        obj = apps.get_model(*model_path.split(".")).objects.filter(file=path).select_related("user").first()
        if obj is None:
            return None
        return {obj.user_id}, obj.user, f"{label} #{obj.pk}"
    return rule


def _ticket_file(path):
    from apps.business_queries.models import TicketAttachment

    if not TicketAttachment.objects.filter(file=path).exists():
        return None
    return "it_team", None, "вложение тикета"


def _shared_image(model_path, field):
    def rule(path):
        from django.apps import apps

        if not apps.get_model(*model_path.split(".")).objects.filter(**{field: path}).exists():
            return None
        return "authenticated", None, ""
    return rule


# В браузере открываются только эти типы; остальное (html, svg, …) — скачиванием, чтобы загруженный файл не выполнил
# скрипт на нашем домене.
INLINE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf", "video/mp4", "video/quicktime"}

# Каталог → правило: path → (кому можно, чьи данные, что открыто) или None, если файла нет в базе.
RULES = {
    "deal_creatives": _deal_file("deals.Deal", "creative_media", "креатив", lambda d: d, ()),
    "deal_chat_files": _deal_file("deals.ChatMessage", "file", "файл из чата", lambda m: m.deal, ("deal",)),
    "deal_evidence": _deal_file("deals.DealEvidence", "file", "доказательство размещения", lambda e: e.deal, ("deal",)),
    "claim_files": _deal_file("deals.ClaimFile", "file", "файл претензии", lambda f: f.claim.deal, ("claim__deal",)),
    "ip_documents": _owned_file("registration.IPApplication", "документ подтверждения статуса"),
    "permits": _owned_file("platforms.PermitDocument", "разрешительный документ"),
    "tickets": _ticket_file,
    "campaign_images": _shared_image("campaigns.Campaign", "image"),
    "blogger_avatars": _shared_image("profiles.BloggerProfile", "avatar"),
    "advertiser_logos": _shared_image("profiles.AdvertiserProfile", "logo"),
}


def _allowed(user, owners):
    if not user.is_authenticated:
        return False
    if owners == "authenticated":
        return True
    if owners == "it_team":
        return user.is_staff and user.groups.filter(name="IT Team").exists()
    return user.is_staff or user.pk in owners


def media_file(request, path):
    path = posixpath.normpath(path).lstrip("/")
    if path.startswith("..") or "/" not in path:
        raise Http404
    rule = RULES.get(path.split("/", 1)[0])
    found = rule(path) if rule else None
    if found is None:
        raise Http404
    owners, subject, label = found
    if not _allowed(request.user, owners):
        raise Http404
    if not default_storage.exists(path):
        raise Http404
    if isinstance(owners, set) and request.user.pk not in owners:
        log_access(request, f"Открыт файл: {label}", subject=subject)
    content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    inline = content_type in INLINE_TYPES
    response = FileResponse(
        default_storage.open(path, "rb"), content_type=content_type if inline else "application/octet-stream",
        as_attachment=not inline, filename=posixpath.basename(path),
    )
    response["Cache-Control"] = "private, max-age=0, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
