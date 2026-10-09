"""#39: категория исполнителя у блогера и подтверждение реквизитов выплаты.

Исполнитель может быть физлицом/самозанятым/ИП/юрлицом; выплата — только на реквизиты, подтверждённые
при верификации, не на ввод в форме вывода.
"""
from django.test import TestCase
from django.urls import reverse

from apps.billing.models import PayoutRequisites
from apps.billing.payout_requisites import (
    PayoutRequisitesError, create_withdrawal_request, expected_requisite_type, submit_payout_requisites,
)
from apps.profiles.models import BloggerProfile
from apps.registration.models import IdentityVerification, IPApplication
from apps.users.models import User


def _blogger(email="bl@test.com"):
    user = User.objects.create_user(email=email, password="pass1234", role=User.Role.BLOGGER, status=User.Status.ACTIVE)
    BloggerProfile.objects.get_or_create(user=user)
    return user


def _verify_identity(user, full_name="Test Bloggerov"):
    return IdentityVerification.objects.create(
        user=user, full_name=full_name, phone="+998900000000", pinfl="00000000000000",
        status=IdentityVerification.Status.VERIFIED,
    )


class CategoryDefaultsTest(TestCase):
    def test_default_category_is_individual(self):
        profile = BloggerProfile.objects.get(user=_blogger())
        self.assertEqual(profile.category, BloggerProfile.Category.INDIVIDUAL)

    def test_ip_category_unavailable_without_confirmed_status(self):
        user = _blogger()
        self.client.force_login(user)
        resp = self.client.post(reverse("web:profile_edit"), {"nickname": "N", "bio": "B", "category": "ip"})
        profile = BloggerProfile.objects.get(user=user)
        self.assertNotEqual(profile.category, BloggerProfile.Category.IP)
        self.assertContains(resp, "Сначала подтвердите статус ИП")

    def test_ip_approval_sets_category(self):
        staff = _blogger("staff@test.com")
        staff.role = User.Role.ADVERTISER
        staff.is_staff = True
        staff.save(update_fields=["role", "is_staff"])
        blogger = _blogger()
        verification = _verify_identity(blogger)
        application = IPApplication.objects.create(
            user=blogger, identity_verification=verification, document_type=IPApplication.DocType.PATENT,
        )
        self.client.force_login(staff)
        self.client.post(reverse("web:admin_ip_application_approve", args=[application.pk]))
        profile = BloggerProfile.objects.get(user=blogger)
        self.assertEqual(profile.category, BloggerProfile.Category.IP)

    def test_ip_approval_does_not_override_explicit_category(self):
        staff = _blogger("staff2@test.com")
        staff.role = User.Role.ADVERTISER
        staff.is_staff = True
        staff.save(update_fields=["role", "is_staff"])
        blogger = _blogger()
        profile = BloggerProfile.objects.get(user=blogger)
        profile.category = BloggerProfile.Category.LEGAL_ENTITY
        profile.save(update_fields=["category"])
        verification = _verify_identity(blogger)
        application = IPApplication.objects.create(
            user=blogger, identity_verification=verification, document_type=IPApplication.DocType.PATENT,
        )
        self.client.force_login(staff)
        self.client.post(reverse("web:admin_ip_application_approve", args=[application.pk]))
        profile.refresh_from_db()
        self.assertEqual(profile.category, BloggerProfile.Category.LEGAL_ENTITY)


class SubmitPayoutRequisitesTest(TestCase):
    def test_requires_verified_identity(self):
        user = _blogger()
        with self.assertRaises(PayoutRequisitesError):
            submit_payout_requisites(user, card_number="8600123456789012", card_holder_name="Test Bloggerov")

    def test_card_requires_16_digits(self):
        user = _blogger()
        _verify_identity(user)
        with self.assertRaises(PayoutRequisitesError):
            submit_payout_requisites(user, card_number="123", card_holder_name="Test Bloggerov")

    def test_card_holder_must_match_identity(self):
        user = _blogger()
        _verify_identity(user, full_name="Test Bloggerov")
        with self.assertRaises(PayoutRequisitesError):
            submit_payout_requisites(user, card_number="8600123456789012", card_holder_name="Someone Else")

    def test_card_submission_succeeds_for_individual(self):
        user = _blogger()
        _verify_identity(user, full_name="Test Bloggerov")
        app = submit_payout_requisites(user, card_number="8600123456789012", card_holder_name="Test Bloggerov")
        self.assertEqual(app.requisite_type, PayoutRequisites.RequisiteType.CARD)
        self.assertEqual(app.status, PayoutRequisites.Status.PENDING)

    def test_ip_category_requires_account_not_card(self):
        user = _blogger()
        _verify_identity(user)
        BloggerProfile.objects.filter(user=user).update(
            is_ip_confirmed=True, category=BloggerProfile.Category.IP,
        )
        fresh_user = User.objects.get(pk=user.pk)
        self.assertEqual(expected_requisite_type(fresh_user), PayoutRequisites.RequisiteType.ACCOUNT)
        app = submit_payout_requisites(
            fresh_user, account_number="12345", bank_mfo="00014", bank_inn="123456789", bank_name="Банк",
        )
        self.assertEqual(app.requisite_type, PayoutRequisites.RequisiteType.ACCOUNT)

    def test_only_one_pending_at_a_time(self):
        user = _blogger()
        _verify_identity(user, full_name="Test Bloggerov")
        submit_payout_requisites(user, card_number="8600123456789012", card_holder_name="Test Bloggerov")
        with self.assertRaises(PayoutRequisitesError):
            submit_payout_requisites(user, card_number="1111222233334444", card_holder_name="Test Bloggerov")


class ApproveRejectPayoutRequisitesTest(TestCase):
    def setUp(self):
        self.staff = _blogger("staff@test.com")
        self.staff.role = User.Role.ADVERTISER
        self.staff.is_staff = True
        self.staff.save(update_fields=["role", "is_staff"])
        self.blogger = _blogger()
        _verify_identity(self.blogger, full_name="Test Bloggerov")
        self.application = submit_payout_requisites(
            self.blogger, card_number="8600123456789012", card_holder_name="Test Bloggerov",
        )

    def test_non_staff_denied(self):
        self.client.force_login(self.blogger)
        resp = self.client.get(reverse("web:admin_payout_requisites"))
        self.assertEqual(resp.status_code, 302)

    def test_staff_sees_pending_queue(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("web:admin_payout_requisites"))
        self.assertIn(self.application, list(resp.context["applications"]))

    def test_approve_notifies_and_becomes_current(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("web:admin_payout_requisites_approve", args=[self.application.pk]))
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, PayoutRequisites.Status.APPROVED)
        from apps.notifications.models import Notification
        self.assertTrue(Notification.objects.filter(
            user=self.blogger, type=Notification.Type.PAYOUT_REQUISITES_APPROVED,
        ).exists())

    def test_reject_requires_reason(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("web:admin_payout_requisites_reject", args=[self.application.pk]), {"reason": ""})
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, PayoutRequisites.Status.PENDING)

    def test_reject_with_reason(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("web:admin_payout_requisites_reject", args=[self.application.pk]), {"reason": "Нечитаемое фото"})
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, PayoutRequisites.Status.REJECTED)
        self.assertEqual(self.application.rejection_reason, "Нечитаемое фото")


class CreateWithdrawalRequestTest(TestCase):
    def test_fails_without_confirmed_requisites(self):
        user = _blogger()
        with self.assertRaises(PayoutRequisitesError):
            create_withdrawal_request(user, 1000)

    def test_succeeds_with_confirmed_requisites(self):
        user = _blogger()
        PayoutRequisites.objects.create(
            user=user, requisite_type=PayoutRequisites.RequisiteType.CARD,
            card_number="8600123456789012", card_holder_name="Test Bloggerov",
            status=PayoutRequisites.Status.APPROVED,
        )
        from apps.billing.models import Wallet
        from decimal import Decimal
        Wallet.objects.update_or_create(user=user, defaults={"available_balance": Decimal("500000")})
        wr = create_withdrawal_request(user, Decimal("100000"))
        self.assertEqual(wr.requisites["card_number"], "8600123456789012")
