"""Обращения по персональным данным и закрытие аккаунта.

Срок ответа — от вида обращения (`DataRequest.DUE_DAYS`). При закрытии удаляются данные, которые нужны только для работы
аккаунта; сделки, расчёты, снимки реквизитов выплат и журналы остаются, потому что у них другое основание хранения.
Закрыть аккаунт нельзя, пока есть незавершённые сделки, претензии, идущие кампании или деньги на балансе.
"""
from django.db import transaction

from apps.notifications.service import NotificationService

from . import consents
from .models import Consent, DataRequest, EmailConfirmationToken, PasswordResetToken, User

KEPT_AFTER_CLOSING = (
    "сделки, расчёты и документы по ним, включая реквизиты проведённых выплат",
    "название компании и ИНН в документах по сделкам",
    "разрешительные документы и материалы рекламных кампаний",
    "журналы согласий и обращений",
)
CLOSED_ANSWER = "Аккаунт закрыт. Удалены профиль, документы статуса, реквизиты выплаты и данные подтверждения личности."


class DataRequestError(Exception):
    """Причина для пользователя или сотрудника."""


def create_request(user, kind, text=""):
    if user.data_requests.filter(kind=kind, status=DataRequest.Status.OPEN).exists():
        raise DataRequestError("Такое обращение уже рассматривается.")
    if kind == DataRequest.Kind.CLOSE:
        blockers = closure_blockers(user)
        if blockers:
            raise DataRequestError("Сейчас аккаунт закрыть нельзя: " + "; ".join(blockers) + ".")
    request = DataRequest.objects.create(user=user, kind=kind, text=text)
    NotificationService.notify_data_request_created(request)
    return request


def closure_blockers(user):
    """Что мешает закрыть аккаунт — списком, понятным пользователю."""
    from apps.billing.models import Wallet
    from apps.campaigns.models import Campaign
    from apps.deals.models import Claim, Deal

    blockers = []
    done = (Deal.Status.COMPLETED, Deal.Status.CANCELLED)
    open_deals = (Deal.objects.filter(advertiser=user) | Deal.objects.filter(blogger=user)).exclude(status__in=done)
    if open_deals.exists():
        blockers.append("есть незавершённые сделки")
    open_claims = (Claim.objects.filter(deal__advertiser=user) | Claim.objects.filter(deal__blogger=user))
    if open_claims.exclude(status=Claim.Status.RESOLVED).exists():
        blockers.append("есть нерассмотренные претензии")
    live = (Campaign.Status.MODERATION, Campaign.Status.ACTIVE, Campaign.Status.PAUSED)
    if Campaign.objects.filter(advertiser=user, status__in=live).exists():
        blockers.append("есть незавершённые кампании")
    if user.payment_invoices.filter(status="issued").exists():
        blockers.append("есть неоплаченные счета")
    wallet = Wallet.objects.filter(user=user).first()
    if wallet and wallet.total_balance > 0:
        blockers.append("на балансе есть деньги")
    return blockers


def _locked_open(pk):
    request = DataRequest.objects.select_for_update().filter(pk=pk).first()
    if request is None or request.status != DataRequest.Status.OPEN:
        raise DataRequestError("Обращение уже рассмотрено.")
    return request


def answer_request(pk, staff, status, answer):
    """Ответить на обращение; закрытие аккаунта подтверждается только через `close_account`."""
    answer = (answer or "").strip()
    if not answer:
        raise DataRequestError("Напишите ответ: он уйдёт пользователю.")
    with transaction.atomic():
        request = _locked_open(pk)
        if status == DataRequest.Status.DONE and request.kind == DataRequest.Kind.CLOSE:
            raise DataRequestError("Закрытие аккаунта подтверждается отдельной кнопкой.")
        request.finish(status, staff, answer)
    NotificationService.notify_data_request_answered(request)
    return request


def _drop_file(files, field):
    if field:
        files.append((field.storage, field.name))


def _clear_personal_data(user, files):
    from apps.billing.models import PayoutRequisites
    from apps.notifications.models import Notification
    from apps.platforms.models import Platform
    from apps.profiles.models import AdvertiserProfile, BloggerProfile
    from apps.registration.models import IdentityVerification, IPApplication

    blogger = BloggerProfile.objects.filter(user=user).first()
    if blogger:
        _drop_file(files, blogger.avatar)
        BloggerProfile.objects.filter(pk=blogger.pk).update(
            nickname="", avatar="", bio="", phone="", pinfl="", show_in_catalog=False,
        )
    advertiser = AdvertiserProfile.objects.filter(user=user).first()
    if advertiser:
        _drop_file(files, advertiser.logo)
        AdvertiserProfile.objects.filter(pk=advertiser.pk).update(
            contact_name="", phone="", website="", logo="", description="",
        )
    IdentityVerification.objects.filter(user=user).update(full_name="", phone="", pinfl="", provider_reference="")
    for application in IPApplication.objects.filter(user=user):
        _drop_file(files, application.file)
    IPApplication.objects.filter(user=user).update(file="", document_number="")
    PayoutRequisites.objects.filter(user=user).delete()
    Platform.objects.filter(blogger=user).update(status=Platform.Status.REJECTED)
    Notification.objects.filter(user=user).delete()
    EmailConfirmationToken.objects.filter(user=user).delete()
    PasswordResetToken.objects.filter(user=user).delete()


def close_account(pk, staff):
    """Подтвердить обращение о закрытии: обезличить аккаунт и удалить данные без другого основания хранения."""
    files = []
    with transaction.atomic():
        request = _locked_open(pk)
        if request.kind != DataRequest.Kind.CLOSE:
            raise DataRequestError("Это обращение не о закрытии аккаунта.")
        user = User.objects.select_for_update().get(pk=request.user_id)
        blockers = closure_blockers(user)
        if blockers:
            raise DataRequestError("Закрыть нельзя: " + "; ".join(blockers) + ".")
        _clear_personal_data(user, files)
        consents.record(Consent.Kind.MARKETING, False, user=user)
        if user.role == User.Role.BLOGGER:
            consents.record(Consent.Kind.CATALOG, False, user=user)
        user.email = f"closed.{user.pk}@closed.internal"
        user.status = User.Status.DELETED
        user.is_active = False
        user.set_unusable_password()
        user.save(update_fields=["email", "status", "is_active", "password"])
        request.finish(DataRequest.Status.DONE, staff, CLOSED_ANSWER)

        def delete_files():
            for storage, name in files:
                storage.delete(name)

        transaction.on_commit(delete_files)
    return request
