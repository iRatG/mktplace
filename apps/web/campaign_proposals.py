"""Правки кампании, предложенные модератором (issue #6).

Значения хранятся как «сырые» данные CampaignForm: при принятии они накладываются на
текущие значения кампании и проходят через ту же форму и ту же проверку параметров, что
и правка рекламодателем — ни разбор сумм, ни правила не дублируются.
"""
from datetime import date
from decimal import Decimal

from django.http import QueryDict

from apps.billing.formatting import format_money
from apps.campaigns.models import Campaign

from .forms import CampaignForm

LIST_FIELDS = ("content_types", "allowed_socials", "evidence_required")

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
    "content_start": "Приём контента с",
    "deadline": "Приём контента до",
    "min_subscribers": "Мин. подписчиков",
    "max_bloggers": "Макс. блогеров",
    "content_types": "Форматы контента",
    "allowed_socials": "Площадки",
    "approval_required": "Согласовать материал перед публикацией",
    "content_lead_days": "Сдать материал за, раб. дней до даты",
    "review_days": "Срок рассмотрения, раб. дней",
    "content_units": "Количество единиц контента",
    "key_message": "Основное сообщение",
    "mandatory_points": "Обязательные тезисы",
    "disclosures": "Обязательные предупреждения и раскрытия",
    "forbidden_phrases": "Запрещённые формулировки",
    "content_restrictions": "Ограничения по тематике и содержанию",
    "visual_requirements": "Требования к визуалу и монтажу",
    "tags_requirements": "Хештеги, ссылки, отметки",
    "acceptance_criteria": "Критерии приёмки",
    "min_retention_days": "Мин. срок сохранения публикации, дней",
    "evidence_required": "Доказательства исполнения",
    "rights_owner": "Исключительные права на контент",
    "reuse_allowed": "Право на репост, таргет, адаптацию",
}

# Условия оферты, которые показываются исполнителю (без служебных полей кампании: бюджет, лимиты, трекинг).
TERMS_FIELDS = (
    "subject", "description", "content_types", "allowed_socials", "content_units", "key_message",
    "mandatory_points", "disclosures", "forbidden_phrases", "content_restrictions", "visual_requirements",
    "tags_requirements", "approval_required", "content_lead_days", "review_days", "acceptance_criteria",
    "min_retention_days", "evidence_required", "rights_owner", "reuse_allowed",
)
# Задание, приёмка и права — показываются отдельным блоком на странице кампании (остальное там уже есть).
CARD_TERMS_FIELDS = (
    "content_units", "key_message", "mandatory_points", "disclosures", "forbidden_phrases", "content_restrictions",
    "visual_requirements", "tags_requirements", "acceptance_criteria", "min_retention_days", "evidence_required",
    "rights_owner", "reuse_allowed",
)


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


def campaign_snapshot(campaign):
    """Параметры кампании в виде JSON-словаря сырых значений формы (списки — списками)."""
    data = form_data_from_campaign(campaign)
    return {
        name: data.getlist(name) if name in LIST_FIELDS else data.get(name, "")
        for name in CampaignForm.Meta.fields
    }


def mark_approved(campaign):
    """Запомнить одобренную версию — вызывать при каждом переходе кампании в ACTIVE после модерации."""
    campaign.approved_snapshot = campaign_snapshot(campaign)


def changes_since_approval(campaign):
    """{поле: {"old", "new"}} — что изменилось с последнего одобрения; None, если одобрения не было."""
    snapshot = campaign.approved_snapshot
    if not snapshot:
        return None
    current = campaign_snapshot(campaign)
    fields = CampaignForm().fields
    changes = {}
    for name in CampaignForm.Meta.fields:
        empty = [] if name in LIST_FIELDS else ""
        old_raw, new_raw = snapshot.get(name, empty), current.get(name, empty)
        if _comparable(fields[name], old_raw) != _comparable(fields[name], new_raw):
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
    campaign.evidence_required = form.cleaned_data.get("evidence_required", [])
    campaign.save()
    return campaign


def _display(field, raw):
    from django import forms

    if isinstance(field, forms.BooleanField):
        return "да" if str(raw).strip().lower() in ("true", "on", "1") else "нет"
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
            return format_money(value)
    except Exception:
        pass
    try:
        return date.fromisoformat(raw).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return raw


def describe(changes):
    """{поле: {"old", "new"}} → [(подпись, было, стало)] в порядке полей формы."""
    fields = CampaignForm().fields
    rows = []
    for name in CampaignForm.Meta.fields:
        if name in changes:
            change = changes[name]
            field = fields[name]
            rows.append((LABELS.get(name, name), _display(field, change["old"]), _display(field, change["new"])))
    return rows


def describe_terms(terms, fields_order=TERMS_FIELDS):
    """[(подпись, значение)] условий оферты из снимка — только заданные поля, в порядке TERMS_FIELDS.
    Поля, которых нет в снимке (оферта направлена до их появления), не показываются."""
    if not terms:
        return []
    fields = CampaignForm().fields
    rows = []
    for name in fields_order:
        if name not in terms:
            continue
        shown = _display(fields[name], terms[name])
        if shown == "—":
            continue
        rows.append((LABELS.get(name, name), shown))
    return rows


def describe_changes(proposal):
    """[(подпись, было, стало)] правок модератора — для рекламодателя и модератора."""
    return describe(proposal.changes)
