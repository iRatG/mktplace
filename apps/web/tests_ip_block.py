"""
Tests for IP blocking (apps.users.blocklist / middleware / unblock_ip command)
and for the welcome email (apps.users.tasks.send_welcome_email).

Covers:
  - middleware: blocked IP gets 403, others are untouched, expired blocks are
    ignored, logged-in staff is never blocked, cache is invalidated on create/delete
  - auto-block: off by default; threshold; only public IPs; never overwrites a
    manual block; honeypot weighs more; strikes come from public forms, login and API
  - unblock_ip management command
  - welcome email: contains bold FAQ and no links, skipped for synthetic addresses,
    queued after email confirmation, a broken queue never breaks confirmation
"""
from io import StringIO
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.registration.models import LegalEntityApplication
from apps.users import blocklist
from apps.users.models import BlockedIP, EmailConfirmationToken, User
from apps.users.tasks import send_welcome_email
from apps.web.views.registration import PUBLIC_FORM_IP_LIMIT as LIMIT

# Публичные адреса: автоблок не трогает приватные и документационные диапазоны.
BAD_IP = "93.184.216.34"
OTHER_IP = "8.8.8.8"


def ip(addr):
    return {"HTTP_X_REAL_IP": addr}


class IPBlockBase(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()


class BlockedIPMiddlewareTest(IPBlockBase):
    def test_unblocked_ip_is_served(self):
        self.assertEqual(self.client.get(reverse("web:login"), **ip(BAD_IP)).status_code, 200)

    def test_manual_block_returns_403_only_for_that_ip(self):
        BlockedIP.objects.create(ip=BAD_IP, reason="тест")
        response = self.client.get(reverse("web:login"), **ip(BAD_IP))
        self.assertEqual(response.status_code, 403)
        self.assertIn("Доступ с вашего адреса ограничен", response.content.decode())
        self.assertEqual(self.client.get(reverse("web:login"), **ip(OTHER_IP)).status_code, 200)

    def test_block_with_future_expiry_is_active(self):
        BlockedIP.objects.create(ip=BAD_IP, blocked_until=timezone.now() + timezone.timedelta(hours=1))
        self.assertEqual(self.client.get(reverse("web:login"), **ip(BAD_IP)).status_code, 403)

    def test_expired_block_is_ignored(self):
        BlockedIP.objects.create(ip=BAD_IP, blocked_until=timezone.now() - timezone.timedelta(minutes=1))
        self.assertEqual(self.client.get(reverse("web:login"), **ip(BAD_IP)).status_code, 200)

    def test_logged_in_staff_is_never_blocked(self):
        staff = User.objects.create_user(email="staff@example.com", password="Good1234!", is_staff=True)
        staff.is_email_confirmed = True
        staff.save()
        self.client.force_login(staff)
        BlockedIP.objects.create(ip=BAD_IP)
        self.assertEqual(self.client.get(reverse("web:login"), **ip(BAD_IP)).status_code, 302)  # уже вошёл

    def test_cache_is_invalidated_when_block_is_added_and_removed(self):
        url = reverse("web:login")
        self.assertEqual(self.client.get(url, **ip(BAD_IP)).status_code, 200)  # «не заблокирован» попал в кэш
        block = BlockedIP.objects.create(ip=BAD_IP)
        self.assertEqual(self.client.get(url, **ip(BAD_IP)).status_code, 403)
        block.delete()
        self.assertEqual(self.client.get(url, **ip(BAD_IP)).status_code, 200)

    def test_bulk_delete_from_admin_invalidates_cache(self):
        BlockedIP.objects.create(ip=BAD_IP)
        self.assertEqual(self.client.get(reverse("web:login"), **ip(BAD_IP)).status_code, 403)
        BlockedIP.objects.filter(ip=BAD_IP).delete()
        self.assertEqual(self.client.get(reverse("web:login"), **ip(BAD_IP)).status_code, 200)


class BlocklistResilienceTest(IPBlockBase):
    def test_site_keeps_working_if_blockedip_table_is_missing(self):
        # Окно при выкатке: новый код уже запущен, а миграция ещё не применена.
        from django.db import ProgrammingError

        with patch("apps.users.models.BlockedIP.objects") as manager, self.assertLogs("apps.users.blocklist", "ERROR"):
            manager.filter.side_effect = ProgrammingError('relation "users_blockedip" does not exist')
            response = self.client.get(reverse("web:login"), **ip(BAD_IP))
        self.assertEqual(response.status_code, 200)


@override_settings(RATELIMIT_ENABLED=True, AUTOBLOCK_ENABLED=True, AUTOBLOCK_STRIKES=10)
class AutoBlockTest(IPBlockBase):
    def test_blocks_at_threshold_for_a_period(self):
        for _ in range(9):
            self.assertFalse(blocklist.record_strike(BAD_IP, "t"))
        self.assertTrue(blocklist.record_strike(BAD_IP, "t"))
        block = BlockedIP.objects.get(ip=BAD_IP)
        self.assertEqual(block.source, BlockedIP.Source.AUTO)
        self.assertIsNotNone(block.blocked_until)
        self.assertIn("Автоблок", block.reason)
        self.assertEqual(self.client.get(reverse("web:login"), **ip(BAD_IP)).status_code, 403)

    def test_other_ip_is_not_affected(self):
        for _ in range(10):
            blocklist.record_strike(BAD_IP, "t")
        self.assertEqual(self.client.get(reverse("web:login"), **ip(OTHER_IP)).status_code, 200)

    @override_settings(AUTOBLOCK_ENABLED=False)
    def test_disabled_by_default_setting(self):
        for _ in range(50):
            self.assertFalse(blocklist.record_strike(BAD_IP, "t"))
        self.assertFalse(BlockedIP.objects.exists())

    def test_never_blocks_private_or_loopback_addresses(self):
        for addr in ("127.0.0.1", "10.0.0.5", "172.18.0.1", "192.168.1.10", "203.0.113.9", "::1", "garbage"):
            for _ in range(20):
                blocklist.record_strike(addr, "t")
        self.assertFalse(BlockedIP.objects.exists())

    def test_does_not_overwrite_manual_block(self):
        BlockedIP.objects.create(ip=BAD_IP, reason="вручную")
        for _ in range(20):
            blocklist.record_strike(BAD_IP, "t")
        block = BlockedIP.objects.get(ip=BAD_IP)
        self.assertEqual(block.source, BlockedIP.Source.MANUAL)
        self.assertIsNone(block.blocked_until)

    def test_expired_auto_block_is_renewed(self):
        BlockedIP.objects.create(
            ip=BAD_IP, source=BlockedIP.Source.AUTO, blocked_until=timezone.now() - timezone.timedelta(hours=1),
        )
        for _ in range(10):
            blocklist.record_strike(BAD_IP, "t")
        self.assertGreater(BlockedIP.objects.get(ip=BAD_IP).blocked_until, timezone.now())

    def test_honeypot_weighs_more_than_a_plain_strike(self):
        url = reverse("web:legal_entity_submit")
        data = {"company_name": "ООО Бот", "inn": "301234567", "hp_note": "http://spam"}
        self.client.post(url, data, **ip(BAD_IP))
        self.assertFalse(BlockedIP.objects.exists())  # 5 штрафов из 10
        self.client.post(url, data, **ip(BAD_IP))
        self.assertTrue(BlockedIP.objects.filter(ip=BAD_IP).exists())
        self.assertEqual(LegalEntityApplication.objects.count(), 0)

    def test_repeated_public_form_limit_hits_lead_to_block(self):
        url = reverse("web:legal_entity_submit")
        for n in range(LIMIT + 10):  # разрешённые + 10 превышений = 10 штрафов
            self.client.post(url, {"company_name": f"ООО {n}", "inn": f"30{n:07d}"}, **ip(BAD_IP))
        self.assertTrue(BlockedIP.objects.filter(ip=BAD_IP).exists())
        self.assertEqual(self.client.get(reverse("web:login"), **ip(BAD_IP)).status_code, 403)

    def test_a_few_limit_hits_do_not_block_a_normal_user(self):
        url = reverse("web:legal_entity_submit")
        for n in range(LIMIT + 3):  # разрешённые + 3 превышения
            self.client.post(url, {"company_name": f"ООО {n}", "inn": f"30{n:07d}"}, **ip(BAD_IP))
        self.assertFalse(BlockedIP.objects.exists())

    def test_repeated_login_lockouts_lead_to_block(self):
        url = reverse("web:login")
        for _ in range(30):  # 20 неудач + 10 попыток при уже сработавшем лимите
            self.client.post(url, {"email": "nobody@example.com", "password": "x"}, **ip(BAD_IP))
        self.assertTrue(BlockedIP.objects.filter(ip=BAD_IP).exists())

    def test_repeated_api_throttling_leads_to_block(self):
        url = reverse("users:login")
        for _ in range(25):  # 10 разрешённых в минуту + превышения
            self.client.post(url, {"email": "nobody@example.com", "password": "x"},
                             content_type="application/json", **ip(BAD_IP))
        self.assertTrue(BlockedIP.objects.filter(ip=BAD_IP).exists())


class UnblockIpCommandTest(IPBlockBase):
    def test_removes_block(self):
        BlockedIP.objects.create(ip=BAD_IP)
        out = StringIO()
        call_command("unblock_ip", BAD_IP, stdout=out)
        self.assertFalse(BlockedIP.objects.exists())
        self.assertIn("разблокирован", out.getvalue())

    def test_unknown_ip_is_an_error(self):
        with self.assertRaises(CommandError):
            call_command("unblock_ip", OTHER_IP)

    def test_lists_active_blocks_only(self):
        BlockedIP.objects.create(ip=BAD_IP, reason="активная")
        BlockedIP.objects.create(ip=OTHER_IP, blocked_until=timezone.now() - timezone.timedelta(hours=1))
        out = StringIO()
        call_command("unblock_ip", stdout=out)
        self.assertIn(BAD_IP, out.getvalue())
        self.assertNotIn(OTHER_IP, out.getvalue())


class WelcomeEmailTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="new@example.com", password="Good1234!")

    def test_email_content_bold_faq_and_no_links(self):
        send_welcome_email(self.user.pk)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        html = message.alternatives[0][0]
        self.assertEqual(message.to, ["new@example.com"])
        self.assertEqual(message.subject, "Добро пожаловать в ublogers")
        self.assertIn("<b>FAQ</b>", html)
        self.assertIn("FAQ", message.body)
        for body in (html, message.body):
            self.assertNotIn("http", body)
            self.assertNotIn("href", body)
        self.assertNotIn("₽", html + message.body)

    def test_not_sent_to_synthetic_sms_addresses(self):
        blogger = User.objects.create_user(email="blogger.998901112233@sms.internal", password="Good1234!")
        send_welcome_email(blogger.pk)
        self.assertEqual(mail.outbox, [])

    def test_unknown_user_is_ignored(self):
        send_welcome_email(999999)
        self.assertEqual(mail.outbox, [])

    def _token(self):
        return EmailConfirmationToken.objects.create(
            user=self.user, expires_at=timezone.now() + timezone.timedelta(hours=1),
        )

    @patch("apps.users.tasks.send_welcome_email.delay")
    def test_queued_after_web_email_confirmation(self, delay):
        token = self._token()
        response = self.client.get(reverse("web:email_confirm", args=[token.token]))
        self.assertEqual(response.status_code, 200)
        delay.assert_called_once_with(self.user.pk)

    @patch("apps.users.tasks.send_welcome_email.delay")
    def test_queued_after_api_email_confirmation(self, delay):
        token = self._token()
        response = self.client.get(reverse("users:email-confirm", args=[token.token]))
        self.assertEqual(response.status_code, 200)
        delay.assert_called_once_with(self.user.pk)

    @patch("apps.users.tasks.send_welcome_email.delay")
    def test_not_queued_for_invalid_token(self, delay):
        import uuid
        self.client.get(reverse("web:email_confirm", args=[uuid.uuid4()]))
        delay.assert_not_called()

    @patch("apps.users.tasks.send_welcome_email.delay", side_effect=ConnectionError("redis down"))
    def test_broken_queue_does_not_break_confirmation(self, delay):
        token = self._token()
        with self.assertLogs("apps.users.tasks", "ERROR"):
            response = self.client.get(reverse("web:email_confirm", args=[token.token]))
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_email_confirmed)
