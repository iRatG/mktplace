"""Согласия: регистрация, журнал, каталог, профиль, принятие новой редакции."""
from unittest import mock

from django.contrib.admin.sites import site
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.platforms.models import Platform
from apps.registration.models import LegalEntityApplication
from apps.registration.services import OneIDResult
from apps.users import consents
from apps.users.admin import ConsentAdmin
from apps.users.models import Consent, PersonalDataAccess, User

INN = "305123456"
BLOGGER_POST = {"full_name": "Иван Иванов", "phone": "+998901234567", "pinfl": "12345678901234"}


def _user(role=User.Role.BLOGGER, email="u@test.local"):
    return User.objects.create_user(email=email, password="Test1234!", role=role, status=User.Status.ACTIVE,
                                    is_email_confirmed=True)


class RegistrationConsentTests(TestCase):
    def test_legal_entity_needs_terms(self):
        response = self.client.post(reverse("web:legal_entity_submit"), {"company_name": "ООО Т", "inn": INN})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(LegalEntityApplication.objects.exists())
        self.assertContains(response, "Без согласия с условиями продолжить нельзя")

    def test_legal_entity_consent_stored_with_application_and_ip(self):
        self.client.post(reverse("web:legal_entity_submit"), {"company_name": "ООО Т", "inn": INN, "accept_terms": "on"},
                         HTTP_X_REAL_IP="10.1.2.3")
        application = LegalEntityApplication.objects.get()
        terms = Consent.objects.get(legal_application=application, kind=Consent.Kind.TERMS)
        self.assertTrue(terms.granted)
        self.assertEqual(terms.ip, "10.1.2.3")
        self.assertIsNone(terms.user)
        self.assertFalse(Consent.objects.get(legal_application=application, kind=Consent.Kind.MARKETING).granted)

    def test_access_issue_moves_consents_to_account(self):
        application = LegalEntityApplication.objects.create(company_name="ООО Т", inn=INN)
        consents.record(Consent.Kind.TERMS, legal_application=application)
        user = _user(User.Role.ADVERTISER)
        consents.attach_to_user(application, user)
        self.assertTrue(consents.is_granted(user, Consent.Kind.TERMS))

    def _submit_blogger(self, **extra):
        with mock.patch("apps.web.views.registration.get_oneid_backend") as backend, \
                mock.patch("apps.web.views.registration.send_blogger_sms_credentials"):
            backend.return_value.verify.return_value = OneIDResult(success=True, reference="r")
            return self.client.post(reverse("web:blogger_identity_submit"), {**BLOGGER_POST, **extra})

    def test_blogger_needs_terms(self):
        self._submit_blogger()
        self.assertFalse(User.objects.filter(role=User.Role.BLOGGER).exists())

    def test_blogger_catalog_is_separate_choice(self):
        self._submit_blogger(accept_terms="on")
        user = User.objects.get(role=User.Role.BLOGGER)
        self.assertTrue(consents.is_granted(user, Consent.Kind.TERMS))
        self.assertFalse(user.blogger_profile.show_in_catalog)
        self.assertFalse(consents.is_granted(user, Consent.Kind.CATALOG))
        self.assertEqual(Consent.objects.filter(user=user, kind=Consent.Kind.CATALOG, granted=False).count(), 1)

    def test_blogger_opts_into_catalog_and_marketing(self):
        self._submit_blogger(accept_terms="on", show_in_catalog="on", accept_marketing="on")
        user = User.objects.get(role=User.Role.BLOGGER)
        self.assertTrue(user.blogger_profile.show_in_catalog)
        self.assertTrue(consents.is_granted(user, Consent.Kind.CATALOG))
        self.assertTrue(consents.is_granted(user, Consent.Kind.MARKETING))

    def test_register_page_has_consents_in_both_tabs(self):
        response = self.client.get(reverse("web:register"))
        self.assertContains(response, 'name="accept_terms"', count=2)
        self.assertContains(response, 'name="show_in_catalog"', count=1)


class ApiRegistrationConsentTests(TestCase):
    url = "/api/v1/auth/register/"
    payload = {"email": "api@test.local", "role": "blogger", "password": "Xy7!plmq2", "password_confirm": "Xy7!plmq2"}

    def setUp(self):
        self.api = APIClient()

    def test_terms_required(self):
        self.assertEqual(self.api.post(self.url, self.payload).status_code, 400)
        self.assertEqual(self.api.post(self.url, {**self.payload, "accept_terms": False}).status_code, 400)
        self.assertFalse(User.objects.filter(email="api@test.local").exists())

    @mock.patch("apps.users.views.send_confirmation_email")
    def test_consent_recorded(self, _send):
        response = self.api.post(self.url, {**self.payload, "accept_terms": True})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(consents.is_granted(User.objects.get(email="api@test.local"), Consent.Kind.TERMS))


class CatalogConsentTests(TestCase):
    def setUp(self):
        self.advertiser = _user(User.Role.ADVERTISER, "adv@test.local")
        self.blogger = _user(email="blog@test.local")
        self.platform = Platform.objects.create(
            blogger=self.blogger, social_type=Platform.SocialType.INSTAGRAM, url="https://instagram.com/c1",
            subscribers=1000, avg_views=100, engagement_rate=3, status=Platform.Status.APPROVED,
        )
        self.client.force_login(self.advertiser)

    def _platforms(self):
        return list(self.client.get(reverse("web:blogger_catalog")).context["platforms"])

    def test_hidden_without_consent(self):
        self.assertEqual(self._platforms(), [])

    def test_shown_with_consent(self):
        consents.set_catalog(self.blogger, True, None)
        self.assertEqual(self._platforms(), [self.platform])

    def test_response_does_not_need_catalog(self):
        self.client.force_login(self.blogger)
        self.assertEqual(self.client.get(reverse("web:catalog")).status_code, 200)


class ProfileConsentTests(TestCase):
    def setUp(self):
        self.blogger = _user()
        self.client.force_login(self.blogger)

    def test_opt_out_and_journal(self):
        consents.set_marketing(self.blogger, True, None)
        consents.set_catalog(self.blogger, True, None)
        response = self.client.post(reverse("web:profile_consents"), {})
        self.assertRedirects(response, reverse("web:profile"))
        self.blogger.blogger_profile.refresh_from_db()
        self.assertFalse(self.blogger.blogger_profile.show_in_catalog)
        self.assertFalse(consents.is_granted(self.blogger, Consent.Kind.MARKETING))
        self.assertEqual(Consent.objects.filter(user=self.blogger, kind=Consent.Kind.MARKETING).count(), 2)

    def test_no_change_no_row(self):
        consents.set_marketing(self.blogger, True, None)
        self.client.post(reverse("web:profile_consents"), {"accept_marketing": "on"})
        self.assertEqual(Consent.objects.filter(user=self.blogger, kind=Consent.Kind.MARKETING).count(), 1)

    def test_advertiser_has_no_catalog_option(self):
        self.client.force_login(_user(User.Role.ADVERTISER, "adv@test.local"))
        response = self.client.get(reverse("web:profile"))
        self.assertNotContains(response, 'name="show_in_catalog"')
        self.assertContains(response, 'name="accept_marketing"')


@override_settings(LEGAL_EDITION="2026-11-01")
class NewEditionTests(TestCase):
    def setUp(self):
        self.user = _user()
        self.client.force_login(self.user)
        self.dashboard = reverse("web:blogger_dashboard")
        self.accept = reverse("web:legal_accept")

    def test_redirects_until_accepted(self):
        response = self.client.get(self.dashboard)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(self.accept))

    def test_accept_returns_to_page_and_stores_edition(self):
        response = self.client.post(self.accept, {"accept_terms": "on", "next": self.dashboard})
        self.assertRedirects(response, self.dashboard)
        self.assertTrue(Consent.objects.filter(user=self.user, kind=Consent.Kind.TERMS, edition="2026-11-01").exists())
        self.assertEqual(self.client.get(self.dashboard).status_code, 200)

    def test_accept_required(self):
        response = self.client.post(self.accept, {"next": self.dashboard})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Consent.objects.filter(user=self.user).exists())

    def test_foreign_next_ignored(self):
        response = self.client.post(self.accept, {"accept_terms": "on", "next": "https://evil.test/"})
        self.assertRedirects(response, self.dashboard, fetch_redirect_response=False)

    def test_old_edition_does_not_count(self):
        Consent.objects.create(user=self.user, kind=Consent.Kind.TERMS, edition="2026-04-24")
        self.assertEqual(self.client.get(self.dashboard).status_code, 302)

    def test_legal_pages_and_logout_stay_open(self):
        self.assertEqual(self.client.get(reverse("web:terms")).status_code, 200)
        self.assertEqual(self.client.post(reverse("web:logout")).status_code, 302)

    def test_staff_not_gated(self):
        staff = User.objects.create_user(email="staff@test.local", password="x", role=User.Role.ADVERTISER,
                                         is_staff=True, status=User.Status.ACTIVE)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("web:admin_dashboard")).status_code, 200)

    @override_settings(LEGAL_EDITION="")
    def test_off_without_edition(self):
        self.assertEqual(self.client.get(self.dashboard).status_code, 200)


class ConsentAdminTests(TestCase):
    def test_read_only_and_export_logged(self):
        staff = User.objects.create_superuser(email="root@test.local", password="x", role=User.Role.ADVERTISER)
        user = _user()
        consents.record(Consent.Kind.TERMS, user=user)
        admin = ConsentAdmin(Consent, site)
        request = RequestFactory().post("/admin/users/consent/")
        request.user = staff
        self.assertFalse(admin.has_add_permission(request))
        self.assertFalse(admin.has_change_permission(request))
        self.assertFalse(admin.has_delete_permission(request))
        response = admin.export_csv(request, Consent.objects.all())
        self.assertIn(user.email, response.content.decode("utf-8-sig"))
        self.assertTrue(PersonalDataAccess.objects.filter(staff=staff).exists())
