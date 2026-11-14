"""Фирменные письма (apps/notifications/emails.py): сборка, адресаты, очередь после фиксации транзакции и события —
аккаунт (подтверждение, сброс, приветствие по роли, ответ поддержки) и деньги (счёт, зачисление, истечение, возврат,
реквизиты, документы для выплаты, выплата)."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.billing import topups
from apps.billing.models import PaymentInvoice, PayoutRequisites
from apps.notifications import emails
from apps.notifications.service import NotificationService
from apps.notifications.tasks import send_rendered_email
from apps.users.models import User
from apps.users.tasks import send_confirmation_email, send_password_reset_email, send_welcome_email

ACCOUNT, MFO = "20208000900111222333", "00401"
OPERATOR = {"OPERATOR_BANK_NAME": "Тест банк", "OPERATOR_ACCOUNT": "20208000000000000099", "OPERATOR_MFO": "00001"}
SITE = {"FRONTEND_URL": "https://ublogers.uz", "SUPPORT_EMAIL": "support@ublogers.uz",
        "CLAIM_EMAIL": "claim@ublogers.uz", "DEFAULT_FROM_EMAIL": "UBlogers! <noreply@ublogers.uz>"}


def _advertiser(email="adv@test.local", inn="305123456"):
    user = User.objects.create_user(email=email, password="x", role=User.Role.ADVERTISER, status=User.Status.ACTIVE)
    user.advertiser_profile.inn = inn
    user.advertiser_profile.save()
    return user


def _blogger(email="blog@test.local"):
    return User.objects.create_user(email=email, password="x", role=User.Role.BLOGGER, status=User.Status.ACTIVE)


def _staff():
    return User.objects.create_user(email="st@test.local", password="x", is_staff=True, status=User.Status.ACTIVE)


class SentMail:
    """Письма, поставленные в очередь внутри блока: задачи выполняются сразу, после фиксации транзакции."""

    def __init__(self, case):
        self.case = case

    def __enter__(self):
        self.patcher = patch("apps.notifications.tasks.send_rendered_email.delay",
                             side_effect=lambda *a: send_rendered_email(*a))
        self.patcher.start()
        self.commit = self.case.captureOnCommitCallbacks(execute=True)
        self.commit.__enter__()
        return self

    def __exit__(self, *exc):
        self.commit.__exit__(*exc)
        self.patcher.stop()
        return False

    def subjects(self):
        return [m.subject for m in mail.outbox]


@override_settings(**SITE)
class LetterTests(TestCase):
    def test_build_html_text_and_footer(self):
        subject, html, text = emails.build("Тема", "Заголовок", intro=["Первый абзац."],
                                           facts=[("Сумма", "5 000 сум"), ("Пусто", "")],
                                           button=("Открыть", "https://ublogers.uz/wallet/"))
        self.assertEqual(subject, "Тема")
        for part in ("Заголовок", "Первый абзац.", "5 000 сум", "https://ublogers.uz/wallet/", "support@ublogers.uz",
                     "claim@ublogers.uz", "ИНН", "Команда UBlogers!"):
            self.assertIn(part, html)
        self.assertNotIn("Пусто", html)
        self.assertIn("Открыть (https://ublogers.uz/wallet/)", text)
        self.assertNotIn("<", text)

    def test_real_addresses_only(self):
        self.assertTrue(emails.is_real_address("a@b.uz"))
        for bad in ("", None, "blogger.998901112233@sms.internal", "legal.305123456@ddocs.internal", "no-at"):
            self.assertFalse(emails.is_real_address(bad))

    def test_account_number_masked(self):
        self.assertEqual(emails.mask_account(ACCOUNT), "20208000 •••• 2333")

    def test_task_sends_with_reply_to_support(self):
        send_rendered_email("a@b.uz", "Тема", "текст", "<p>html</p>")
        message = mail.outbox[0]
        self.assertEqual((message.to, message.reply_to), (["a@b.uz"], ["support@ublogers.uz"]))
        self.assertEqual(message.from_email, "UBlogers! <noreply@ublogers.uz>")
        self.assertEqual(message.alternatives[0][1], "text/html")

    def test_nothing_before_commit_and_broken_queue_is_harmless(self):
        with patch("apps.notifications.tasks.send_rendered_email.delay", side_effect=RuntimeError("queue down")) as d:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertTrue(emails.deliver("a@b.uz", emails.build("Т", "З")))
                d.assert_not_called()
            d.assert_called_once()
        self.assertEqual(mail.outbox, [])

    def test_payout_documents_letter(self):
        from types import SimpleNamespace
        deal = SimpleNamespace(pk=245, contract_number="UB-245", campaign=SimpleNamespace(name="Распродажа"))
        subject, html, text = emails.payout_documents(deal, Decimal("900000"), "чек самозанятого")
        self.assertEqual(subject, "Для выплаты по сделке UB-245 нужны документы — UBlogers!")
        self.assertIn("чек самозанятого", text)
        self.assertIn(reverse("web:deal_detail", args=[245]), text)

    def test_broken_factory_never_raises(self):
        self.assertFalse(emails.send("a@b.uz", lambda: 1 / 0))


@override_settings(**SITE)
class AccountEmailTests(TestCase):
    def test_confirmation_and_reset(self):
        user = _advertiser()
        send_confirmation_email(user.pk)
        send_password_reset_email(user.pk)
        first, second = mail.outbox
        self.assertEqual(first.subject, "Подтвердите email — UBlogers!")
        self.assertIn("https://ublogers.uz/confirm-email/", first.alternatives[0][0])
        self.assertEqual(second.subject, "Сброс пароля — UBlogers!")
        self.assertIn("https://ublogers.uz/password-reset/", second.body)
        self.assertEqual(first.reply_to, ["support@ublogers.uz"])

    def test_welcome_by_role(self):
        send_welcome_email(_advertiser().pk)
        send_welcome_email(_blogger().pk)
        adv, blog = mail.outbox
        self.assertEqual(adv.subject, "Добро пожаловать в UBlogers!")
        self.assertIn("Пополните баланс", adv.body)
        self.assertIn(reverse("web:advertiser_dashboard"), adv.body)
        self.assertIn("Подтвердите статус", blog.body)
        self.assertIn(reverse("web:profile"), blog.body)
        self.assertNotIn("Алина", adv.body + blog.body)

    def test_welcome_skips_service_addresses(self):
        send_welcome_email(_blogger("blogger.998901112233@sms.internal").pk)
        send_welcome_email(_advertiser("legal.305123456@ddocs.internal").pk)
        self.assertEqual(mail.outbox, [])

    @patch("apps.notifications.tasks.send_support_message_email.delay")
    def test_support_reply_only_to_signed_in_user(self, _support):
        data = {"name": "Иван", "email": "stranger@example.com", "message": "Вопрос"}
        with SentMail(self):
            self.client.post(reverse("web:support"), data)
        self.assertEqual(mail.outbox, [])
        user = _advertiser("me@example.com")
        self.client.force_login(user)
        with SentMail(self):
            self.client.post(reverse("web:support"), data)
        self.assertEqual([m.to for m in mail.outbox], [["me@example.com"]])
        self.assertEqual(mail.outbox[0].subject, "Мы получили ваше обращение — UBlogers!")


@override_settings(**SITE, **OPERATOR)
class MoneyEmailTests(TestCase):
    def setUp(self):
        self.user = _advertiser()
        self.staff = _staff()

    def _paid(self):
        invoice = topups.create_invoice(self.user, Decimal("1000000"))
        topups.confirm_payment(invoice, self.staff, Decimal("1000000"), date(2026, 11, 14), ACCOUNT, MFO)
        return invoice

    def test_invoice_issued_and_credited(self):
        with SentMail(self) as sent:
            invoice = self._paid()
        self.assertEqual(sent.subjects(), [f"Счёт {invoice.number} на 1 000 000 сум — UBlogers!",
                                           "Деньги зачислены на баланс — UBlogers!"])
        self.assertIn(reverse("web:invoice_print", args=[invoice.pk]), mail.outbox[0].body)
        self.assertIn("14.11.2026", mail.outbox[1].body)

    def test_invoice_expired(self):
        invoice = topups.create_invoice(self.user, Decimal("500000"))
        PaymentInvoice.objects.filter(pk=invoice.pk).update(expires_at=timezone.now() - timezone.timedelta(days=1))
        with SentMail(self) as sent:
            topups.expire_overdue()
        self.assertEqual(sent.subjects(), [f"Срок оплаты счёта {invoice.number} истёк — UBlogers!"])

    def test_refund_letters(self):
        self._paid()
        mail.outbox.clear()
        with SentMail(self):
            refund = topups.request_refund(self.user, Decimal("300000"), ACCOUNT, MFO)
            topups.ask_for_details(refund, self.staff, "Подтвердите номер счёта.")
            refund = topups.update_details(refund, self.user, ACCOUNT, MFO)
            topups.mark_refund_sent(refund, self.staff)
            refund.refresh_from_db()
            topups.complete_refund(refund, self.staff)
        self.assertEqual([m.subject for m in mail.outbox], [
            "Заявка на возврат 300 000 сум принята — UBlogers!",
            "Для возврата нужны сведения — UBlogers!",
            "Заявка на возврат 300 000 сум принята — UBlogers!",
            "Возврат 300 000 сум проведён — UBlogers!",
        ])
        self.assertIn("20208000 •••• 2333", mail.outbox[0].body)
        self.assertIn("Подтвердите номер счёта.", mail.outbox[1].body)
        self.assertTrue(all(m.to == ["adv@test.local"] for m in mail.outbox))

    def test_refund_rejected(self):
        self._paid()
        refund = topups.request_refund(self.user, Decimal("300000"), ACCOUNT, MFO)
        mail.outbox.clear()
        with SentMail(self):
            topups.reject_refund(refund, self.staff, "Счёт не совпадает со счётом поступления.")
        self.assertEqual(mail.outbox[0].subject, "Возврат не проведён — UBlogers!")
        self.assertIn("Счёт не совпадает", mail.outbox[0].body)


@override_settings(**SITE)
class PayoutEmailTests(TestCase):
    def setUp(self):
        self.blogger = _blogger()

    def test_requisites_approved_and_rejected(self):
        app = PayoutRequisites.objects.create(user=self.blogger, requisite_type=PayoutRequisites.RequisiteType.ACCOUNT,
                                              account_number=ACCOUNT, bank_mfo=MFO, rejection_reason="Нет выписки банка.")
        with SentMail(self) as sent:
            NotificationService.notify_payout_requisites_approved(self.blogger, app)
            NotificationService.notify_payout_requisites_rejected(self.blogger, app)
        self.assertEqual(sent.subjects(), ["Реквизиты для выплат подтверждены — UBlogers!",
                                           "Реквизиты для выплат не подтверждены — UBlogers!"])
        self.assertIn("•••• 2333", mail.outbox[0].body)
        self.assertIn("Нет выписки банка.", mail.outbox[1].body)

    def test_payout_done_and_failed(self):
        with SentMail(self) as sent:
            NotificationService.notify_withdrawal_approved(self.blogger, Decimal("900000"))
            NotificationService.notify_withdrawal_rejected(self.blogger, Decimal("900000"), "Счёт закрыт.")
        self.assertEqual(sent.subjects(), ["Выплата 900 000 сум проведена — UBlogers!", "Выплата не прошла — UBlogers!"])
        self.assertIn("Счёт закрыт.", mail.outbox[1].body)
        self.assertEqual(mail.outbox[0].to, ["blog@test.local"])

    def test_no_letters_to_service_address(self):
        sms = _blogger("blogger.998901112233@sms.internal")
        with SentMail(self):
            NotificationService.notify_withdrawal_approved(sms, Decimal("100"))
        self.assertEqual(mail.outbox, [])
