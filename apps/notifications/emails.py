"""Фирменные письма UBlogers!: один шаблон (templates/emails/letter.html на основе base_email.html), одна функция на событие.

Письмо собирается сразу (суммы и статусы — на момент события), а отправляется Celery-задачей
`send_rendered_email` после фиксации транзакции: откат не рассылает писем, сбой почты или очереди не ломает действие.
Письма дополняют уведомления в кабинете и уходят только на настоящие адреса (не на служебные адреса входа по SMS
и юрлиц по ИНН). Ответ на письмо попадает в поддержку (Reply-To = SUPPORT_EMAIL).

События вызываются из NotificationService (сайт, API и задачи по расписанию получают одинаковые письма) и из
apps.billing.topups (счёт выставлен, заявка на возврат принята).
"""
import html
import logging
import re
from urllib.parse import urlparse

from django.conf import settings
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.billing.formatting import format_money

logger = logging.getLogger(__name__)

SERVICE_DOMAINS = ("@sms.internal", "@ddocs.internal")


def is_real_address(email):
    """Служебные адреса (вход по SMS, юрлица по ИНН) — не почтовые ящики."""
    email = (email or "").strip().lower()
    return bool(email) and "@" in email and not email.endswith(SERVICE_DOMAINS)


def site_url(path=""):
    return settings.FRONTEND_URL.rstrip("/") + path


def brand():
    return {
        "support_email": settings.SUPPORT_EMAIL,
        "claim_email": settings.CLAIM_EMAIL,
        "site_url": site_url("/"),
        "site_host": urlparse(settings.FRONTEND_URL).netloc or settings.FRONTEND_URL,
        "operator_name": settings.OPERATOR_NAME,
        "operator_inn": settings.OPERATOR_INN,
        "operator_address": settings.OPERATOR_ADDRESS,
    }


def money(value):
    return f"{format_money(value)} сум"


def day(moment):
    return timezone.localtime(moment).strftime("%d.%m.%Y") if moment else ""


def mask_account(number):
    number = (number or "").replace(" ", "")
    return f"{number[:8]} •••• {number[-4:]}" if len(number) > 12 else number


def html_to_text(markup):
    """Текстовая версия письма: абзацы, строки таблицы, ссылка кнопки в скобках."""
    text = re.sub(r"(?is)<(head|style|title)[^>]*>.*?</\1>", "", markup)
    text = re.sub(r'(?is)<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                  lambda m: m.group(2) if m.group(1).startswith("mailto:") else f"{m.group(2)} ({m.group(1)})", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|h1|tr)>", "\n", text)
    text = re.sub(r"(?i)</td>", "  ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def build(subject, heading, *, intro=(), steps=(), facts=(), button=None, outro=(), notes=()):
    """Собирает письмо: (тема, HTML, текст). button — (надпись, абсолютный адрес)."""
    context = {
        "subject": subject, "heading": heading, "intro": list(intro), "steps": list(steps),
        "facts": [(label, value) for label, value in facts if value not in (None, "")],
        "button_label": button[0] if button else "", "button_url": button[1] if button else "",
        "outro": list(outro), "notes": list(notes), "brand": brand(),
    }
    markup = render_to_string("emails/letter.html", context)
    return subject, markup, html_to_text(markup)


def deliver(to, letter):
    """Ставит письмо в очередь после фиксации транзакции. Ошибка очереди — только в журнал."""
    if not is_real_address(to):
        return False
    subject, markup, text = letter

    def enqueue():
        from .tasks import send_rendered_email
        try:
            send_rendered_email.delay(to, subject, text, markup)
        except Exception:
            logger.exception("Не удалось поставить письмо в очередь: %s", subject)

    transaction.on_commit(enqueue)
    return True


def send(to, factory, *args, **kwargs):
    """Собрать письмо и поставить в очередь. Любая ошибка — только в журнал: письмо не ломает действие."""
    try:
        letter = factory(*args, **kwargs)
        return bool(letter) and deliver(to, letter)
    except Exception:
        logger.exception("Письмо %s не собрано", getattr(factory, "__name__", factory))
        return False


# ── Аккаунт и помощь ──────────────────────────────────────────────────────────

def email_confirmation(user, url):
    return build(
        "Подтвердите email — UBlogers!", "Подтвердите email",
        intro=["Вы зарегистрировались на платформе UBlogers! — рекламе у блогеров Узбекистана. Чтобы войти в кабинет, "
               "подтвердите адрес почты."],
        button=("Подтвердить email", url),
        notes=[f"Ссылка действует 24 часа. Если кнопка не работает, скопируйте ссылку в браузер: {url}",
               "Если вы не регистрировались на UBlogers!, просто удалите это письмо."],
    )


def password_reset(user, url):
    return build(
        "Сброс пароля — UBlogers!", "Сброс пароля",
        intro=["Мы получили запрос на сброс пароля для вашей учётной записи."],
        button=("Задать новый пароль", url),
        notes=[f"Ссылка действует 1 час. Если кнопка не работает, скопируйте ссылку в браузер: {url}",
               "Если вы не запрашивали сброс, ничего не делайте: пароль останется прежним.",
               "Сотрудники UBlogers! никогда не спрашивают ваш пароль и коды подтверждения."],
    )


def welcome(user):
    """Приветствие по роли: рекламодателю — как начать кампанию, исполнителю — как начать получать заказы."""
    common = "Ответы на частые вопросы — в разделе «Помощь» на сайте. Если что-то непонятно, просто ответьте на это письмо."
    if user.role == user.Role.ADVERTISER:
        return build(
            "Добро пожаловать в UBlogers!", "Добро пожаловать в UBlogers!",
            intro=["Спасибо, что выбрали UBlogers! Здесь вы находите блогеров для рекламы, договариваетесь об условиях "
                   "и платите только за принятый результат."],
            steps=[("Пополните баланс", "по счёту на оплату — раздел «Кошелёк». Деньги зачисляем после поступления на счёт."),
                   ("Создайте кампанию:", "задание, сроки, бюджет, критерии приёмки. Модератор проверит её перед публикацией."),
                   ("Выберите исполнителя", "из откликов или каталога и согласуйте с ним условия на платформе. После вашего "
                                           "подтверждения платформа направит исполнителю предложение."),
                   ("Примите публикацию.", "Деньги хранятся на платформе и уходят исполнителю только после публикации и приёмки.")],
            button=("Перейти в кабинет", site_url(reverse("web:advertiser_dashboard"))),
            outro=["Все переговоры, правки и договорённости с исполнителями ведите только на платформе — так защищены и "
                   "ваши деньги, и условия сделки.", common],
        )
    return build(
        "Добро пожаловать в UBlogers!", "Добро пожаловать в UBlogers!",
        intro=["Спасибо, что присоединились к UBlogers! Здесь рекламодатели находят блогеров, а вы получаете заказы и "
               "оплату через платформу — без переписки в мессенджерах и рисков неоплаты."],
        steps=[("Подтвердите статус", "— ИП, самозанятый или юрлицо — и реквизиты для выплат в профиле."),
               ("Добавьте площадки:", "Telegram, Instagram, YouTube, TikTok. Мы проверим их и покажем рекламодателям."),
               ("Откликайтесь на кампании.", "До принятия предложения вы видите задание, цену и все условия."),
               ("Опубликуйте материал", "и получите оплату на подтверждённые реквизиты после приёмки.")],
        button=("Заполнить профиль", site_url(reverse("web:profile"))),
        outro=[common],
    )


def support_received(sent_at):
    return build(
        "Мы получили ваше обращение — UBlogers!", "Мы получили ваше обращение",
        intro=["Спасибо, что написали. Обращение получено, ответим на этот адрес не позднее 30 календарных дней — "
               "обычно гораздо быстрее."],
        facts=[("Получено", timezone.localtime(sent_at).strftime("%d.%m.%Y, %H:%M"))],
        outro=["Если хотите что-то добавить — просто ответьте на это письмо."],
        notes=["Претензии по сделкам подавайте на странице сделки — они рассматриваются в сроки Правил резервирования. "
               "Обращения о персональных данных — через раздел «Мои данные»."],
    )


# ── Счета и деньги ────────────────────────────────────────────────────────────

def _wallet_button():
    return ("Открыть кошелёк", site_url(reverse("web:wallet")))


def invoice_issued(invoice):
    return build(
        f"Счёт {invoice.number} на {money(invoice.amount)} — UBlogers!", "Счёт на оплату выставлен",
        intro=["Вы сформировали счёт на пополнение баланса."],
        facts=[("Счёт", invoice.number), ("Сумма", money(invoice.amount)), ("Оплатить до", day(invoice.expires_at)),
               ("Назначение платежа", invoice.purpose)],
        button=("Открыть счёт для печати", site_url(reverse("web:invoice_print", args=[invoice.pk]))),
        outro=["Оплатите со счёта вашей организации и укажите назначение платежа точно как в счёте — так мы быстрее "
               "найдём перевод. Сумма появится на балансе после фактического поступления, мы пришлём письмо."],
        notes=["Деньги поступают на отдельный счёт для расчётов по сделкам и учитываются на вашем балансе. Само "
               "пополнение комиссией не облагается. Неиспользованный остаток можно вернуть на счёт, с которого он поступил."],
    )


def balance_topped_up(invoice):
    return build(
        "Деньги зачислены на баланс — UBlogers!", "Деньги зачислены на баланс",
        intro=["Перевод по счёту поступил, сумма доступна для кампаний."],
        facts=[("Счёт", invoice.number), ("Зачислено", money(invoice.received_amount)),
               ("Дата поступления", invoice.received_on.strftime("%d.%m.%Y") if invoice.received_on else "")],
        button=_wallet_button(),
    )


def invoice_expired(invoice):
    return build(
        f"Срок оплаты счёта {invoice.number} истёк — UBlogers!", "Срок оплаты счёта истёк",
        intro=[f"Срок оплаты счёта {invoice.number} на {money(invoice.amount)} закончился {day(invoice.expires_at)}.",
               "Если вы уже перевели деньги — ничего делать не нужно: мы зачислим их, как только перевод поступит. "
               "Если нет — сформируйте новый счёт в кошельке."],
        button=("Сформировать новый счёт", site_url(reverse("web:wallet"))),
    )


def refund_requested(refund):
    return build(
        f"Заявка на возврат {money(refund.amount)} принята — UBlogers!", "Заявка на возврат принята",
        intro=["Мы приняли заявку на возврат свободного остатка. Эта сумма больше не доступна для новых кампаний, "
               "пока идёт возврат."],
        facts=[("Сумма", money(refund.amount)), ("На счёт", f"{mask_account(refund.account)}, МФО {refund.mfo}"),
               ("Передадим в банк до", day(refund.due_at))],
        outro=["Если для возврата не хватит сведений, сообщим об этом не позднее 2 рабочих дней. Передумали? Заявку "
               "можно отозвать в кошельке, пока она не передана в банк."],
        button=_wallet_button(),
    )


def refund_changed(refund):
    """Письмо по новому статусу возврата; для «передано в банк» и «отозвано» письма нет."""
    from apps.billing.models import BalanceRefund

    status = refund.status
    if status == BalanceRefund.Status.NEEDS_INFO:
        return build(
            "Для возврата нужны сведения — UBlogers!", "Для возврата нужны сведения",
            intro=[f"Чтобы провести возврат {money(refund.amount)}, нам не хватает сведений:"],
            facts=[("Что нужно", refund.staff_comment)],
            outro=["Срок передачи в банк — 5 рабочих дней — начнётся заново, когда вы дополните заявку."],
            button=("Дополнить заявку", site_url(reverse("web:wallet"))),
        )
    if status == BalanceRefund.Status.DONE:
        return build(
            f"Возврат {money(refund.amount)} проведён — UBlogers!", "Возврат проведён",
            intro=["Банк подтвердил перевод — возврат завершён."],
            facts=[("Сумма", money(refund.amount)), ("На счёт", f"{mask_account(refund.account)}, МФО {refund.mfo}"),
                   ("Дата", day(refund.processed_at or timezone.now()))],
            button=_wallet_button(),
        )
    if status == BalanceRefund.Status.REJECTED:
        return build(
            "Возврат не проведён — UBlogers!", "Возврат не проведён",
            intro=[f"Мы не смогли провести возврат {money(refund.amount)}. Сумма снова доступна на балансе."],
            facts=[("Причина", refund.staff_comment)],
            outro=["Подайте новую заявку с правильными реквизитами или ответьте на это письмо — поможем разобраться."],
            button=_wallet_button(),
        )
    return None


def requisites_approved(application):
    return build(
        "Реквизиты для выплат подтверждены — UBlogers!", "Реквизиты для выплат подтверждены",
        intro=["Мы проверили и подтвердили ваши реквизиты. Выплаты по сделкам будут приходить на них."],
        facts=[("Реквизиты", f"{application.get_requisite_type_display()} {application.masked_number()}".strip())],
        notes=["Если реквизиты изменятся, отправьте новые в профиле: выплаты, которые уже в работе, уйдут на прежние."],
    )


def requisites_rejected(application):
    return build(
        "Реквизиты для выплат не подтверждены — UBlogers!", "Реквизиты не подтверждены",
        intro=["Мы не смогли подтвердить реквизиты для выплат."],
        facts=[("Причина", application.rejection_reason or "не указана")],
        button=("Исправить реквизиты", site_url(reverse("web:payout_requisites"))),
    )


def payout_documents(deal, amount, missing):
    return build(
        f"Для выплаты по сделке {deal.contract_number} нужны документы — UBlogers!", "Для выплаты нужны документы",
        intro=[f"Публикация по сделке {deal.contract_number} принята, выплата подготовлена. Чтобы мы её провели, не хватает:"],
        facts=[("Сделка", f"{deal.contract_number}, кампания «{deal.campaign.name}»"), ("К выплате", money(amount)),
               ("Не хватает", missing)],
        outro=["Мы проведём выплату не позднее 5 рабочих дней после того, как всё будет готово."],
        button=("Открыть сделку", site_url(reverse("web:deal_detail", args=[deal.pk]))),
    )


def payout_done(amount, deal=None):
    facts = [("Сделка", f"{deal.contract_number}, кампания «{deal.campaign.name}»") if deal else ("Выплата", "остаток на балансе")]
    if deal is not None and deal.amount and deal.amount != amount:
        facts += [("Стоимость услуг", money(deal.amount)), ("Удержано", money(deal.amount - amount))]
    facts += [("Переведено", money(amount)), ("Дата", day(timezone.now()))]
    return build(
        f"Выплата {money(amount)} проведена — UBlogers!", "Выплата проведена",
        intro=["Мы перевели оплату на ваши реквизиты."],
        facts=facts, button=_wallet_button(),
        notes=["Расчёт по сделке — на странице сделки."] if deal else [],
    )


def payout_failed(amount, comment="", deal=None):
    where = f" по сделке {deal.contract_number}" if deal is not None else ""
    return build(
        f"Выплата{where} не прошла — UBlogers!", "Выплата не прошла",
        intro=[f"Выплата{where} не проведена. Сумма сохранена — мы переведём её повторно, когда причина будет устранена."],
        facts=[("К выплате", money(amount)), ("Причина", comment)],
        outro=["Обновите реквизиты в профиле или ответьте на это письмо."],
        button=("Обновить реквизиты", site_url(reverse("web:payout_requisites"))),
    )
