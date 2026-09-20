"""
Tests for bot/abuse protection (apps.users.security, apps.users.throttling).

Covers:
  - client_ip: trusts X-Real-IP (set by nginx), ignores a client-forged
    X-Forwarded-For, falls back to REMOTE_ADDR on garbage
  - login: per-IP limit on failed attempts (429), successful logins not counted,
    other IPs unaffected
  - password reset: per-IP limit, per-email limit silently stops sending letters,
    honeypot sends nothing
  - legal_entity_submit / blogger_identity_submit: per-IP limit, honeypot creates
    nothing, per-phone limit for bloggers
  - Turnstile: enabled only when a secret key is set; missing/invalid token blocks,
    valid token passes, our-side failures (network, wrong secret) skip the captcha
  - API /api/v1/auth/: scoped throttling
"""
import json
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from apps.registration.models import IdentityVerification, LegalEntityApplication
from apps.users import security
from apps.users.models import User
from apps.web.views.registration import PUBLIC_FORM_IP_LIMIT as LIMIT

IP_A = {"HTTP_X_REAL_IP": "203.0.113.10"}
IP_B = {"HTTP_X_REAL_IP": "203.0.113.20"}


@override_settings(RATELIMIT_ENABLED=True)
class BotProtectionBase(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()


class ClientIpTest(TestCase):
    def test_uses_x_real_ip(self):
        request = RequestFactory().get("/", HTTP_X_REAL_IP="203.0.113.10", REMOTE_ADDR="10.0.0.1")
        self.assertEqual(security.client_ip(request), "203.0.113.10")

    def test_ignores_forged_x_forwarded_for(self):
        request = RequestFactory().get(
            "/", HTTP_X_FORWARDED_FOR="1.2.3.4", HTTP_X_REAL_IP="203.0.113.10", REMOTE_ADDR="10.0.0.1",
        )
        self.assertEqual(security.client_ip(request), "203.0.113.10")

    def test_falls_back_to_remote_addr_on_garbage(self):
        request = RequestFactory().get("/", HTTP_X_REAL_IP="not-an-ip", REMOTE_ADDR="10.0.0.1")
        self.assertEqual(security.client_ip(request), "10.0.0.1")

    def test_falls_back_to_remote_addr_without_header(self):
        request = RequestFactory().get("/", REMOTE_ADDR="10.0.0.1")
        self.assertEqual(security.client_ip(request), "10.0.0.1")


class HitTest(BotProtectionBase):
    def test_exceeds_only_after_limit(self):
        results = [security.hit("t", "x", 3, 60) for _ in range(5)]
        self.assertEqual(results, [False, False, False, True, True])

    def test_scopes_and_idents_are_independent(self):
        for _ in range(4):
            security.hit("t", "x", 3, 60)
        self.assertFalse(security.hit("t", "y", 3, 60))
        self.assertFalse(security.hit("other", "x", 3, 60))

    def test_is_limited_does_not_count(self):
        for _ in range(10):
            self.assertFalse(security.is_limited("t", "x", 3))

    @override_settings(RATELIMIT_ENABLED=False)
    def test_disabled_never_limits(self):
        self.assertFalse(any(security.hit("t", "x", 1, 60) for _ in range(5)))


class LoginRateLimitTest(BotProtectionBase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(email="u@example.com", password="Good1234!")
        self.user.is_email_confirmed = True
        self.user.save()
        self.url = reverse("web:login")

    def _fail(self, ip=IP_A, email="nobody@example.com"):
        return self.client.post(self.url, {"email": email, "password": "wrong"}, **ip)

    def test_blocks_after_twenty_failures_from_one_ip(self):
        for _ in range(20):
            self.assertEqual(self._fail().status_code, 200)
        response = self._fail()
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Слишком много попыток", status_code=429)

    def test_correct_password_also_blocked_while_limited(self):
        for _ in range(20):
            self._fail()
        response = self.client.post(self.url, {"email": "u@example.com", "password": "Good1234!"}, **IP_A)
        self.assertEqual(response.status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_other_ip_is_not_affected(self):
        for _ in range(21):
            self._fail(IP_A)
        self.assertEqual(self._fail(IP_B).status_code, 200)

    def test_forged_x_forwarded_for_does_not_bypass(self):
        for i in range(20):
            self.client.post(
                self.url, {"email": "nobody@example.com", "password": "wrong"},
                HTTP_X_FORWARDED_FOR=f"9.9.9.{i}", **IP_A,
            )
        response = self.client.post(
            self.url, {"email": "nobody@example.com", "password": "wrong"},
            HTTP_X_FORWARDED_FOR="8.8.8.8", **IP_A,
        )
        self.assertEqual(response.status_code, 429)

    def test_successful_logins_do_not_count(self):
        for _ in range(25):
            self.client.post(self.url, {"email": "u@example.com", "password": "Good1234!"}, **IP_A)
            self.client.post(reverse("web:logout"))
        response = self.client.post(self.url, {"email": "u@example.com", "password": "Good1234!"}, **IP_A)
        self.assertEqual(response.status_code, 302)


class PasswordResetProtectionTest(BotProtectionBase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(email="u@example.com", password="Good1234!")
        self.url = reverse("web:password_reset")

    @patch("apps.web.views.auth.send_password_reset_email")
    def test_ip_limit_returns_429(self, task):
        for i in range(5):
            self.assertEqual(self.client.post(self.url, {"email": f"a{i}@example.com"}, **IP_A).status_code, 200)
        response = self.client.post(self.url, {"email": "a6@example.com"}, **IP_A)
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Слишком много попыток", status_code=429)

    @patch("apps.web.views.auth.send_password_reset_email")
    def test_email_limit_stops_sending_but_looks_like_success(self, task):
        # Разные IP — сработать должен именно лимит на email.
        for i in range(5):
            ip = {"HTTP_X_REAL_IP": f"203.0.113.{100 + i}"}
            response = self.client.post(self.url, {"email": "u@example.com"}, **ip)
            self.assertContains(response, "Письмо отправлено")
        self.assertEqual(task.delay.call_count, 3)

    @patch("apps.web.views.auth.send_password_reset_email")
    def test_honeypot_sends_nothing(self, task):
        response = self.client.post(self.url, {"email": "u@example.com", "hp_note": "http://spam"}, **IP_A)
        self.assertContains(response, "Письмо отправлено")
        task.delay.assert_not_called()

    @patch("apps.web.views.auth.send_password_reset_email")
    def test_normal_request_still_works(self, task):
        response = self.client.post(self.url, {"email": "u@example.com"}, **IP_A)
        self.assertContains(response, "Письмо отправлено")
        task.delay.assert_called_once_with(self.user.pk)


class LegalEntityProtectionTest(BotProtectionBase):
    def setUp(self):
        super().setUp()
        self.url = reverse("web:legal_entity_submit")

    def _post(self, n, ip=IP_A, **extra):
        data = {"company_name": f"ООО Тест {n}", "inn": f"30{n:07d}"}
        data.update(extra)
        return self.client.post(self.url, data, **ip)

    def test_per_ip_limit(self):
        for n in range(LIMIT):
            self.assertEqual(self._post(n).status_code, 200)
        self.assertEqual(LegalEntityApplication.objects.count(), LIMIT)
        response = self._post(LIMIT + 1)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(LegalEntityApplication.objects.count(), LIMIT)

    def test_other_ip_is_not_affected(self):
        for n in range(LIMIT + 1):
            self._post(n, IP_A)
        self.assertEqual(self._post(LIMIT + 5, IP_B).status_code, 200)

    def test_honeypot_creates_no_application_but_looks_like_success(self):
        response = self._post(1, hp_note="http://spam")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["submitted"])
        self.assertEqual(LegalEntityApplication.objects.count(), 0)


class BloggerIdentityProtectionTest(BotProtectionBase):
    def setUp(self):
        super().setUp()
        self.url = reverse("web:blogger_identity_submit")

    def _post(self, phone="+998901112233", pinfl="12345678901234", ip=IP_A, **extra):
        data = {"full_name": "Тест Тестов", "phone": phone, "pinfl": pinfl}
        data.update(extra)
        return self.client.post(self.url, data, **ip)

    def test_per_ip_limit(self):
        for i in range(LIMIT):
            self._post(phone=f"+99890111{i:04d}", pinfl=f"1234567890{i:04d}")
        response = self._post(phone="+998909999999", pinfl="99999999999999")
        self.assertEqual(response.status_code, 429)

    @patch("apps.web.views.registration.get_oneid_backend")
    def test_per_phone_limit_across_ips(self, backend):
        backend.return_value.verify.return_value = MagicMock(success=False, failure_reason="x", reference="")
        for i in range(5):
            ip = {"HTTP_X_REAL_IP": f"203.0.113.{100 + i}"}
            self.assertEqual(self._post(pinfl=f"1234567890{i:04d}", ip=ip).status_code, 200)
        response = self._post(pinfl="55555555555555", ip={"HTTP_X_REAL_IP": "203.0.113.200"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(backend.return_value.verify.call_count, 5)

    @patch("apps.web.views.registration.get_oneid_backend")
    def test_per_pinfl_limit_across_ips(self, backend):
        backend.return_value.verify.return_value = MagicMock(success=False, failure_reason="x", reference="")
        for i in range(5):
            ip = {"HTTP_X_REAL_IP": f"203.0.113.{100 + i}"}
            self.assertEqual(self._post(phone=f"+99890111{i:04d}", ip=ip).status_code, 200)
        response = self._post(phone="+998909999999", ip={"HTTP_X_REAL_IP": "203.0.113.200"})
        self.assertEqual(response.status_code, 429)

    @patch("apps.web.views.registration.get_oneid_backend")
    def test_honeypot_never_calls_oneid_or_creates_records(self, backend):
        response = self._post(hp_note="http://spam")
        self.assertEqual(response.status_code, 200)
        backend.assert_not_called()
        self.assertEqual(IdentityVerification.objects.count(), 0)
        self.assertEqual(User.objects.count(), 0)

    def test_normal_registration_still_works(self):
        response = self._post()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.filter(role=User.Role.BLOGGER).count(), 1)


@override_settings(RATELIMIT_ENABLED=True, TURNSTILE_SECRET_KEY="test-secret", TURNSTILE_SITE_KEY="test-site")
class TurnstileTest(BotProtectionBase):
    def setUp(self):
        super().setUp()
        self.url = reverse("web:legal_entity_submit")
        self.data = {"company_name": "ООО Капча", "inn": "301234567"}

    def _urlopen(self, success=True, exc=None, error_codes=()):
        payload = {"success": success, "error-codes": list(error_codes)}
        ctx = MagicMock()
        ctx.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        return patch("apps.users.security.urllib.request.urlopen", return_value=ctx, side_effect=exc)

    def test_widget_rendered_when_configured(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'data-sitekey="test-site"')

    @override_settings(TURNSTILE_SECRET_KEY="", TURNSTILE_SITE_KEY="")
    def test_widget_absent_and_form_works_when_not_configured(self):
        self.assertNotContains(self.client.get(self.url), "cf-turnstile")
        self.client.post(self.url, self.data, **IP_A)
        self.assertEqual(LegalEntityApplication.objects.count(), 1)

    def test_missing_token_blocks(self):
        response = self.client.post(self.url, self.data, **IP_A)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "проверку на робота", status_code=400)
        self.assertEqual(LegalEntityApplication.objects.count(), 0)

    def test_invalid_token_blocks(self):
        with self._urlopen(success=False, error_codes=["invalid-input-response"]):
            response = self.client.post(self.url, {**self.data, "cf-turnstile-response": "bad"}, **IP_A)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(LegalEntityApplication.objects.count(), 0)

    def test_valid_token_passes(self):
        with self._urlopen(success=True):
            self.client.post(self.url, {**self.data, "cf-turnstile-response": "ok"}, **IP_A)
        self.assertEqual(LegalEntityApplication.objects.count(), 1)

    def test_cloudflare_unreachable_does_not_break_registration(self):
        # Проблема на нашей стороне не должна закрывать регистрацию всем.
        with self._urlopen(exc=OSError("network down")), self.assertLogs("apps.users.security", "ERROR"):
            self.client.post(self.url, {**self.data, "cf-turnstile-response": "ok"}, **IP_A)
        self.assertEqual(LegalEntityApplication.objects.count(), 1)

    def test_wrong_secret_key_does_not_break_registration(self):
        with self._urlopen(success=False, error_codes=["invalid-input-secret"]), self.assertLogs("apps.users.security", "ERROR"):
            self.client.post(self.url, {**self.data, "cf-turnstile-response": "ok"}, **IP_A)
        self.assertEqual(LegalEntityApplication.objects.count(), 1)

    def _http_error(self, code, body):
        import io
        import urllib.error
        return urllib.error.HTTPError("url", code, "err", {}, io.BytesIO(json.dumps(body).encode()))

    def test_http_400_with_bad_token_code_still_blocks(self):
        # Cloudflare может ответить HTTP 400 на плохой токен — это бот, а не наш сбой.
        exc = self._http_error(400, {"success": False, "error-codes": ["invalid-input-response"]})
        with self._urlopen(exc=exc):
            response = self.client.post(self.url, {**self.data, "cf-turnstile-response": "garbage"}, **IP_A)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(LegalEntityApplication.objects.count(), 0)

    def test_http_400_with_invalid_secret_does_not_break_registration(self):
        exc = self._http_error(400, {"success": False, "error-codes": ["invalid-input-secret"]})
        with self._urlopen(exc=exc), self.assertLogs("apps.users.security", "ERROR"):
            self.client.post(self.url, {**self.data, "cf-turnstile-response": "ok"}, **IP_A)
        self.assertEqual(LegalEntityApplication.objects.count(), 1)

    def test_missing_token_still_blocked_even_if_cloudflare_is_down(self):
        # Отсутствие токена — это бот, а не сбой Cloudflare: сеть не спрашиваем, блокируем.
        with self._urlopen(exc=OSError("network down")):
            response = self.client.post(self.url, self.data, **IP_A)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(LegalEntityApplication.objects.count(), 0)

    def test_login_is_not_protected_by_captcha(self):
        # Вход капчей не закрыт: неверные ключи не должны запирать всех пользователей.
        response = self.client.post(reverse("web:login"), {"email": "x@example.com", "password": "y"}, **IP_A)
        self.assertEqual(response.status_code, 200)


class ApiThrottleTest(BotProtectionBase):
    def test_login_endpoint_throttled_after_ten_per_minute(self):
        url = reverse("users:login")
        body = {"email": "nobody@example.com", "password": "wrong"}
        codes = [self.client.post(url, body, content_type="application/json", **IP_A).status_code for _ in range(12)]
        self.assertNotIn(429, codes[:10])
        self.assertEqual(codes[10:], [429, 429])

    def test_register_endpoint_throttled_after_five_per_hour(self):
        url = reverse("users:register")
        codes = []
        with patch("apps.users.views.send_confirmation_email"):
            for i in range(7):
                body = {"email": f"n{i}@example.com", "password": "Good1234!", "password_confirm": "Good1234!", "role": "blogger"}
                codes.append(self.client.post(url, body, content_type="application/json", **IP_A).status_code)
        self.assertEqual(codes[5:], [429, 429])

    @override_settings(RATELIMIT_ENABLED=False)
    def test_disabled_flag_turns_throttling_off(self):
        url = reverse("users:login")
        body = {"email": "nobody@example.com", "password": "wrong"}
        codes = {self.client.post(url, body, content_type="application/json", **IP_A).status_code for _ in range(15)}
        self.assertNotIn(429, codes)
