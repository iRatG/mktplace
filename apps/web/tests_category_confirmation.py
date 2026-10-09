"""#48: подтверждение категорий «самозанятый» и «юрлицо-исполнитель» документами,
по тому же механизму, что и статус ИП (#39)."""
from django.test import TestCase
from django.urls import reverse

from apps.notifications.models import Notification
from apps.profiles.models import BloggerProfile
from apps.registration.models import IdentityVerification, IPApplication
from apps.users.models import User


def _blogger(email="bl@test.com"):
    user = User.objects.create_user(email=email, password="pass1234", role=User.Role.BLOGGER, status=User.Status.ACTIVE)
    BloggerProfile.objects.get_or_create(user=user)
    return user


def _staff(email="staff@test.com"):
    return User.objects.create_user(
        email=email, password="pass1234", role=User.Role.ADVERTISER, is_staff=True, status=User.Status.ACTIVE,
    )


def _verify_identity(user, full_name="Test Bloggerov"):
    return IdentityVerification.objects.create(
        user=user, full_name=full_name, phone="+998900000000", pinfl="00000000000000",
        status=IdentityVerification.Status.VERIFIED,
    )


class CategoryGatingTest(TestCase):
    def test_self_employed_unavailable_without_confirmed_status(self):
        user = _blogger()
        self.client.force_login(user)
        resp = self.client.post(
            reverse("web:profile_edit"), {"nickname": "N", "bio": "B", "category": "self_employed"},
        )
        profile = BloggerProfile.objects.get(user=user)
        self.assertNotEqual(profile.category, BloggerProfile.Category.SELF_EMPLOYED)
        self.assertContains(resp, "Сначала подтвердите статус самозанятого")

    def test_legal_entity_unavailable_without_confirmed_status(self):
        user = _blogger()
        self.client.force_login(user)
        resp = self.client.post(
            reverse("web:profile_edit"), {"nickname": "N", "bio": "B", "category": "legal_entity"},
        )
        profile = BloggerProfile.objects.get(user=user)
        self.assertNotEqual(profile.category, BloggerProfile.Category.LEGAL_ENTITY)
        self.assertContains(resp, "Сначала подтвердите статус юрлица-исполнителя")


class ApplicationSubmitTest(TestCase):
    def test_upload_requires_verified_identity(self):
        user = _blogger()
        self.client.force_login(user)
        resp = self.client.post(reverse("web:ip_application_upload"), {
            "target_category": "self_employed",
            "document_type": IPApplication.DocType.SELF_EMPLOYED_CERT,
        })
        self.assertEqual(IPApplication.objects.filter(user=user).count(), 0)
        self.assertRedirects(resp, reverse("web:blogger_dashboard"))

    def test_upload_self_employed_application(self):
        user = _blogger()
        _verify_identity(user)
        self.client.force_login(user)
        from django.core.files.uploadedfile import SimpleUploadedFile
        resp = self.client.post(reverse("web:ip_application_upload"), {
            "target_category": "self_employed",
            "document_type": IPApplication.DocType.SELF_EMPLOYED_CERT,
            "file": SimpleUploadedFile("cert.pdf", b"data", content_type="application/pdf"),
        })
        self.assertRedirects(resp, reverse("web:ip_application_list"))
        application = IPApplication.objects.get(user=user)
        self.assertEqual(application.target_category, IPApplication.TargetCategory.SELF_EMPLOYED)

    def test_upload_legal_entity_application_with_inn(self):
        user = _blogger()
        _verify_identity(user)
        self.client.force_login(user)
        from django.core.files.uploadedfile import SimpleUploadedFile
        resp = self.client.post(reverse("web:ip_application_upload"), {
            "target_category": "legal_entity",
            "document_type": IPApplication.DocType.LEGAL_ENTITY_EXTRACT,
            "document_number": "123456789",
            "file": SimpleUploadedFile("extract.pdf", b"data", content_type="application/pdf"),
        })
        self.assertRedirects(resp, reverse("web:ip_application_list"))
        application = IPApplication.objects.get(user=user)
        self.assertEqual(application.target_category, IPApplication.TargetCategory.LEGAL_ENTITY)
        self.assertEqual(application.document_number, "123456789")


class ApproveRejectTest(TestCase):
    def setUp(self):
        self.staff = _staff()
        self.blogger = _blogger()
        self.verification = _verify_identity(self.blogger)

    def _application(self, target_category, doc_type):
        return IPApplication.objects.create(
            user=self.blogger, identity_verification=self.verification,
            target_category=target_category, document_type=doc_type,
        )

    def test_approve_self_employed_sets_flag_and_category(self):
        application = self._application(
            IPApplication.TargetCategory.SELF_EMPLOYED, IPApplication.DocType.SELF_EMPLOYED_CERT,
        )
        self.client.force_login(self.staff)
        self.client.post(reverse("web:admin_ip_application_approve", args=[application.pk]))
        profile = BloggerProfile.objects.get(user=self.blogger)
        self.assertTrue(profile.is_self_employed_confirmed)
        self.assertEqual(profile.category, BloggerProfile.Category.SELF_EMPLOYED)
        application.refresh_from_db()
        self.assertEqual(application.status, IPApplication.Status.APPROVED)
        self.assertTrue(Notification.objects.filter(user=self.blogger).exists())

    def test_approve_legal_entity_sets_flag_and_category(self):
        application = self._application(
            IPApplication.TargetCategory.LEGAL_ENTITY, IPApplication.DocType.LEGAL_ENTITY_EXTRACT,
        )
        self.client.force_login(self.staff)
        self.client.post(reverse("web:admin_ip_application_approve", args=[application.pk]))
        profile = BloggerProfile.objects.get(user=self.blogger)
        self.assertTrue(profile.is_legal_entity_confirmed)
        self.assertEqual(profile.category, BloggerProfile.Category.LEGAL_ENTITY)

    def test_approve_does_not_override_explicit_category(self):
        profile = BloggerProfile.objects.get(user=self.blogger)
        profile.category = BloggerProfile.Category.IP
        profile.save(update_fields=["category"])
        application = self._application(
            IPApplication.TargetCategory.SELF_EMPLOYED, IPApplication.DocType.SELF_EMPLOYED_CERT,
        )
        self.client.force_login(self.staff)
        self.client.post(reverse("web:admin_ip_application_approve", args=[application.pk]))
        profile.refresh_from_db()
        self.assertTrue(profile.is_self_employed_confirmed)
        self.assertEqual(profile.category, BloggerProfile.Category.IP)

    def test_reject_self_employed_requires_reason(self):
        application = self._application(
            IPApplication.TargetCategory.SELF_EMPLOYED, IPApplication.DocType.SELF_EMPLOYED_CERT,
        )
        self.client.force_login(self.staff)
        self.client.post(reverse("web:admin_ip_application_reject", args=[application.pk]), {"rejection_reason": ""})
        application.refresh_from_db()
        self.assertEqual(application.status, IPApplication.Status.PENDING)

    def test_reject_legal_entity_with_reason(self):
        application = self._application(
            IPApplication.TargetCategory.LEGAL_ENTITY, IPApplication.DocType.LEGAL_ENTITY_EXTRACT,
        )
        self.client.force_login(self.staff)
        self.client.post(
            reverse("web:admin_ip_application_reject", args=[application.pk]),
            {"rejection_reason": "Нечитаемый документ"},
        )
        application.refresh_from_db()
        self.assertEqual(application.status, IPApplication.Status.REJECTED)
        self.assertEqual(application.rejection_reason, "Нечитаемый документ")
        profile = BloggerProfile.objects.get(user=self.blogger)
        self.assertFalse(profile.is_legal_entity_confirmed)

    def test_queue_lists_all_target_categories_together(self):
        ip_app = self._application(IPApplication.TargetCategory.IP, IPApplication.DocType.PATENT)
        se_app = self._application(IPApplication.TargetCategory.SELF_EMPLOYED, IPApplication.DocType.SELF_EMPLOYED_CERT)
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("web:admin_ip_applications"))
        apps_in_queue = set(resp.context["applications"])
        self.assertEqual(apps_in_queue, {ip_app, se_app})
