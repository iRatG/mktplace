"""Правки кампании, предложенные модератором (issue #6).

Значения хранятся как «сырые» данные CampaignForm: при принятии они накладываются на
текущие значения кампании и проходят через ту же форму и ту же проверку параметров, что
и правка рекламодателем — ни разбор сумм, ни правила не дублируются.
"""
from datetime import date
from decimal import Decimal

from django.http import QueryDict

from apps.campaigns.models import Campaign

from .forms import CampaignForm

LIST_FIELDS = ("content_types", "allowed_socials")

LABELS = {
    "name": "Название",
    "description": "Описание",
    "category": "Категория",
    "subject": "Что рекламируем",
    "payment_type": "Тип оплаты",
    "fixed_price": "Цена за размещение",
    "budget": "Общий бюджет",
    "cpa_type": "Тип конверсии CPA",
    "cpa_rate": "Ставка CPA",
    "cpa_tracking_url": "Ссылка трекинга",
    "start_date": "Начало",
    "end_date": "Окончание",
    "deadline": "Дедлайн контента",
    "min_subscribers": "Мин. подписчиков",
    "max_bloggers": "Макс. блогеров",
    "content_types": "Форматы контента",
    "allowed_socials": "Площадки",
}


def _raw(value):
    """Значение поля → строка, как её прислал бы браузер."""
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value.normalize(), "f") if value == value.to_integral() else str(value)
    if isinstance(value, Campaign) or hasattr(value, "pk"):
        return str(value.pk)
    return str(value)


def form_data_from_campaign(campaign):
    """Текущие значения кампании в виде данных формы (QueryDict, списки — через setlist)."""
    data = QueryDict(mutable=True)
    for name in CampaignForm.Meta.fields:
        if name in LIST_FIELDS:
            data.setlist(name, [str(v) for v in (getattr(campaign, name) or [])])
        elif name == "category":
            data[name] = str(campaign.category_id or "")
        else:
            data[name] = _raw(getattr(campaign, name))
    return data


def _comparable(field, raw):
    try:
        value = field.clean(raw)
    except Exception:
        return raw
    if hasattr(value, "pk"):
        return value.pk
    if isinstance(value, (list, tuple)):
        return sorted(str(v) for v in value)
    if value in ("", None):
        return None
    return value


def compute_changes(campaign, form):
    """{поле: {"old": raw, "new": raw}} только для изменённых полей валидной формы."""
    old = form_data_from_campaign(campaign)
    changes = {}
    for name in CampaignForm.Meta.fields:
        field = form.fields[name]
        if name in LIST_FIELDS:
            old_raw, new_raw = old.getlist(name), form.data.getlist(name)
        else:
            old_raw, new_raw = old.get(name, ""), form.data.get(name, "")
        if _comparable(field, old_raw) != _comparable(field, new_raw):
            changes[name] = {"old": old_raw, "new": new_raw}
    return changes


def proposal_form(proposal):
    """CampaignForm с текущими значениями кампании и наложенными правками (для применения)."""
    data = form_data_from_campaign(proposal.campaign)
    for name, change in proposal.changes.items():
        if name in LIST_FIELDS:
            data.setlist(name, list(change["new"]))
        else:
            data[name] = change["new"]
    return CampaignForm(data, instance=proposal.campaign)


def save_campaign_form(form):
    """Сохранить CampaignForm так же, как правку рекламодателем (JSON-списки отдельно)."""
    campaign = form.save(commit=False)
    campaign.content_types = form.cleaned_data.get("content_types", [])
    campaign.allowed_socials = form.cleaned_data.get("allowed_socials", [])
    campaign.save()
    return campaign


def _display(field, raw):
    if raw in ("", None, []):
        return "—"
    if isinstance(raw, list):
        choices = dict(getattr(field, "choices", []))
        return ", ".join(str(choices.get(v, v)) for v in raw)
    if hasattr(field, "queryset"):
        obj = field.queryset.filter(pk=raw).first()
        return str(obj) if obj else raw
    choices = dict(getattr(field, "choices", []) or [])
    if raw in choices:
        return str(choices[raw])
    try:
        value = Decimal(str(raw).replace(" ", ""))
        if value == value.to_integral():
            return f"{int(value):,}".replace(",", " ")
    except Exception:
        pass
    try:
        return date.fromisoformat(raw).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return raw


def describe_changes(proposal):
    """[(подпись, было, стало)] для показа рекламодателю и модератору."""
    fields = CampaignForm().fields
    rows = []
    for name in CampaignForm.Meta.fields:
        if name in proposal.changes:
            change = proposal.changes[name]
            field = fields[name]
            rows.append((LABELS.get(name, name), _display(field, change["old"]), _display(field, change["new"])))
    return rows
