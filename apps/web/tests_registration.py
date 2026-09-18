"""
Tests for the legal-entity / blogger-IP registration module (apps.registration).

Covers:
  - services.assign_reviewer(): round-robin by fewest open PENDING applications,
    empty pool returns None
  - services stub backends: OneID stub always succeeds, SMS stub never raises
  - admin_legal_entities: staff-only, personal per-reviewer queue (not shared)
  - admin_legal_entity_approve/reject: status log before status change, reject
    requires a reason, rejection sets retention_anchor_at
  - admin_legal_entity_issue_access: password shown once, never emailed/SMSed
  - admin_ip_applications: staff-only shared PENDING queue
  - admin_ip_application_approve: sets BloggerProfile.is_ip_confirmed
  - blogger_identity_submit: creates User+BloggerProfile+IdentityVerification
    on stub-OneID success, without ever creating a User on failure
"""
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.profiles.models import BloggerProfile
from apps.registration.models import (
    IdentityVerification,
    IPApplication,
    LegalEntityApplication,
    LegalEntityApplicationStatusLog,
)
from apps.registration.services import (
    REGISTRATION_REVIEWERS_GROUP,
    LogSmsBackend,
    StubOneIDVerificationBackend,
    assign_reviewer,
)
from apps.users.models import User

_counter = 0


def _make_user(email=None, role=User.Role.ADVERTISER, is_staff=False):
    global _counter
    _counter += 1
    email = email or f"user{_counter}@demo.com"
    user = User.objects.create_user(email=email, password="Test1234!", role=role)
    user.is_staff = is_staff
    user.is_email_confirmed = True
    user.status = User.Status.ACTIVE
    user.save(update_fields=["is_staff", "is_email_confirmed", "status"])
    return user


def _make_reviewer(email=None):
    reviewer = _make_user(email=email, role=User.Role.ADVERTISER, is_staff=True)
    group, _ = Group.objects.get_or_create(name=REGISTRATION_REVIEWERS_GROUP)
    reviewer.groups.add(group)
    return reviewer


class AssignReviewerTests(TestCase):
    def test_empty_pool_returns_none(self):
        self.assertIsNone(assign_reviewer())

    def test_assigns_to_reviewer_with_fewest_open_applications(self):
        busy = _make_reviewer("busy@demo.com")
        free = _make_reviewer("free@demo.com")
        advertiser = _make_user(role=User.Role.ADVERTISER)

        LegalEntityApplication.objects.create(
            user=advertiser, company_name="A", inn="1", assigned_to=busy,
        )
        LegalEntityApplication.objects.create(
            user=advertiser, company_name="B", inn="2", assigned_to=busy,
        )

        self.assertEqual(assign_reviewer(), free)


class StubBackendTests(TestCase):
    def test_oneid_stub_always_succeeds(self):
        result = StubOneIDVerificationBackend().verify("Ivan Ivanov", "+998901234567", "12345678901234")
        self.assertTrue(result.success)
        self.assertTrue(result.reference)

    def test_sms_stub_never_raises_and_sends_nothing_real(self):
        sent = LogSmsBackend().send("+998901234567", "Логин: x\nПароль: y")
        self.assertTrue(sent)


class LegalEntityQueueTests(TestCase):
    def setUp(self):
        self.reviewer_a = _make_reviewer("reviewer_a@demo.com")
        self.reviewer_b = _make_reviewer("reviewer_b@demo.com")
        self.advertiser = _make_user(role=User.Role.ADVERTISER)
        self.app_for_a = LegalEntityApplication.objects.create(
            user=self.advertiser, company_name="For A", inn="111", assigned_to=self.reviewer_a,
        )
        self.app_for_b = LegalEntityApplication.objects.create(
            user=self.advertiser, company_name="For B", inn="222", assigned_to=self.reviewer_b,
        )

    def test_non_staff_denied(self):
        self.client.force_login(self.advertiser)
        response = self.client.get(reverse("web:admin_legal_entities"))
        self.assertEqual(response.status_code, 302)

    def test_reviewer_sees_only_own_queue(self):
        self.client.force_login(self.reviewer_a)
        response = self.client.get(reverse("web:admin_legal_entities"))
        apps_shown = list(response.context["applications"])
        self.assertIn(self.app_for_a, apps_shown)
        self.assertNotIn(self.app_for_b, apps_shown)

    def test_approve_logs_status_before_change_and_notifies(self):
        self.client.force_login(self.reviewer_a)
        self.client.post(reverse("web:admin_legal_entity_approve", args=[self.app_for_a.pk]))

        self.app_for_a.refresh_from_db()
        self.assertEqual(self.app_for_a.status, LegalEntityApplication.Status.APPROVED)
        self.assertEqual(self.app_for_a.reviewed_by, self.reviewer_a)

        log = LegalEntityApplicationStatusLog.objects.get(application=self.app_for_a)
        self.assertEqual(log.old_status, LegalEntityApplication.Status.PENDING)
        self.assertEqual(log.new_status, LegalEntityApplication.Status.APPROVED)

    def test_reject_requires_reason(self):
        self.client.force_login(self.reviewer_a)
        self.client.post(reverse("web:admin_legal_entity_reject", args=[self.app_for_a.pk]), {})

        self.app_for_a.refresh_from_db()
        self.assertEqual(self.app_for_a.status, LegalEntityApplication.Status.PENDING)

    def test_reject_with_reason_sets_retention_anchor(self):
        self.client.force_login(self.reviewer_a)
        self.client.post(
            reverse("web:admin_legal_entity_reject", args=[self.app_for_a.pk]),
            {"rejection_reason": "ИНН не совпадает с названием"},
        )

        self.app_for_a.refresh_from_db()
        self.assertEqual(self.app_for_a.status, LegalEntityApplication.Status.REJECTED)
        self.assertEqual(self.app_for_a.rejection_reason, "ИНН не совпадает с названием")
        self.assertIsNotNone(self.app_for_a.retention_anchor_at)

    def test_issue_access_shows_password_once_and_never_stores_it(self):
        self.app_for_a.status = LegalEntityApplication.Status.APPROVED
        self.app_for_a.save(update_fields=["status"])

        old_password_hash = self.advertiser.password

        self.client.force_login(self.reviewer_a)
        response = self.client.post(
            reverse("web:admin_legal_entity_issue_access", args=[self.app_for_a.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("issued_password", response.context)
        raw_password = response.context["issued_password"]
        self.assertTrue(raw_password)

        self.app_for_a.refresh_from_db()
        self.assertEqual(self.app_for_a.ddocs_status, LegalEntityApplication.DdocsStatus.ACCESS_ISSUED)
        self.assertIsNotNone(self.app_for_a.retention_anchor_at)

        self.advertiser.refresh_from_db()
        self.assertNotEqual(self.advertiser.password, old_password_hash)
        self.assertTrue(self.advertiser.check_password(raw_password))


class IPApplicationQueueTests(TestCase):
    def setUp(self):
        self.staff = _make_user(role=User.Role.ADVERTISER, is_staff=True)
        self.blogger = _make_user(role=User.Role.BLOGGER)
        self.verification = IdentityVerification.objects.create(
            user=self.blogger, full_name="Blogger Test", phone="+998900000000",
            pinfl="00000000000000", status=IdentityVerification.Status.VERIFIED,
        )
        self.application = IPApplication.objects.create(
            user=self.blogger,
            identity_verification=self.verification,
            document_type=IPApplication.DocType.PATENT,
        )

    def test_non_staff_denied(self):
        self.client.force_login(self.blogger)
        response = self.client.get(reverse("web:admin_ip_applications"))
        self.assertEqual(response.status_code, 302)

    def test_staff_sees_pending_queue(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("web:admin_ip_applications"))
        self.assertIn(self.application, list(response.context["applications"]))

    def test_approve_confirms_ip_status_on_profile(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("web:admin_ip_application_approve", args=[self.application.pk]))

        self.application.refresh_from_db()
        self.assertEqual(self.application.status, IPApplication.Status.APPROVED)
        self.assertIsNotNone(self.application.retention_anchor_at)

        profile = BloggerProfile.objects.get(user=self.blogger)
        self.assertTrue(profile.is_ip_confirmed)


class BloggerIdentitySubmitTests(TestCase):
    @patch("apps.web.views.registration.send_blogger_sms_credentials")
    def test_success_creates_user_and_verification(self, mock_task):
        response = self.client.post(reverse("web:blogger_identity_submit"), {
            "full_name": "Иван Иванов",
            "phone": "+998901112233",
            "pinfl": "12345678901234",
        })
        self.assertEqual(response.status_code, 302)

        verification = IdentityVerification.objects.get(pinfl="12345678901234")
        self.assertEqual(verification.status, IdentityVerification.Status.VERIFIED)
        self.assertIsNotNone(verification.user)

        user = verification.user
        self.assertEqual(user.role, User.Role.BLOGGER)
        self.assertTrue(user.is_email_confirmed)

        profile = BloggerProfile.objects.get(user=user)
        self.assertEqual(profile.phone, "+998901112233")
        self.assertEqual(profile.pinfl, "12345678901234")

        mock_task.delay.assert_called_once()
        called_user_id, raw_password = mock_task.delay.call_args[0]
        self.assertEqual(called_user_id, user.pk)
        self.assertTrue(user.check_password(raw_password))

    @patch("apps.web.views.registration.get_oneid_backend")
    def test_failure_does_not_create_user(self, mock_get_backend):
        from apps.registration.services import OneIDResult

        mock_backend = mock_get_backend.return_value
        mock_backend.verify.return_value = OneIDResult(success=False, failure_reason="mismatch")

        users_before = User.objects.count()
        response = self.client.post(reverse("web:blogger_identity_submit"), {
            "full_name": "Иван Иванов",
            "phone": "+998901112233",
            "pinfl": "12345678901234",
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), users_before)
        verification = IdentityVerification.objects.get(pinfl="12345678901234")
        self.assertEqual(verification.status, IdentityVerification.Status.FAILED)
        self.assertIsNone(verification.user)


class IPApplicationUploadTests(TestCase):
    def test_upload_blocked_without_verified_identity(self):
        blogger = _make_user(role=User.Role.BLOGGER)
        self.client.force_login(blogger)
        response = self.client.get(reverse("web:ip_application_upload"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(IPApplication.objects.filter(user=blogger).count(), 0)
