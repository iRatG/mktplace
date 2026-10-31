"""Одна учётная запись на категорию: повторная регистрация и обход блокировки."""
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.profiles.models import BloggerProfile
from apps.registration.models import LegalEntityApplication
from apps.registration.services import REGISTRATION_REVIEWERS_GROUP, legal_entity_login
from apps.users.models import User

PINFL = "12345678901234"
INN = "305123456"


def _blogger_post(client, phone="+998901112233", pinfl=PINFL):
    return client.post(reverse("web:blogger_identity_submit"), {
        "full_name": "Иван Иванов", "phone": phone, "pinfl": pinfl, "accept_terms": "on",
    })


def _legal_post(client, inn=INN):
    return client.post(reverse("web:legal_entity_submit"), {"company_name": "ООО Т", "inn": inn, "accept_terms": "on"})


class BloggerDuplicateTests(TestCase):
    def test_same_pinfl_other_phone_refused(self):
        _blogger_post(self.client)
        response = _blogger_post(self.client, phone="+998907778899")
        self.assertContains(response, "уже зарегистрирован")
        self.assertEqual(User.objects.filter(role=User.Role.BLOGGER).count(), 1)

    def test_repeat_after_lost_sms_still_works(self):
        _blogger_post(self.client)
        response = _blogger_post(self.client)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.filter(role=User.Role.BLOGGER).count(), 1)

    def test_blocked_cannot_unblock_with_same_phone(self):
        _blogger_post(self.client)
        user = User.objects.get(role=User.Role.BLOGGER)
        User.objects.filter(pk=user.pk).update(status=User.Status.BLOCKED)
        password = User.objects.get(pk=user.pk).password
        response = _blogger_post(self.client)
        self.assertContains(response, "Регистрация недоступна")
        user.refresh_from_db()
        self.assertEqual((user.status, user.password), (User.Status.BLOCKED, password))

    def test_blocked_cannot_return_with_new_phone(self):
        _blogger_post(self.client)
        User.objects.filter(role=User.Role.BLOGGER).update(status=User.Status.BLOCKED)
        self.assertContains(_blogger_post(self.client, phone="+998907778899"), "Регистрация недоступна")
        self.assertEqual(User.objects.filter(role=User.Role.BLOGGER).count(), 1)

    def test_closed_account_can_return(self):
        _blogger_post(self.client)
        BloggerProfile.objects.update(pinfl="", phone="")
        User.objects.filter(role=User.Role.BLOGGER).update(email="closed.1@closed.internal", status=User.Status.DELETED)
        self.assertEqual(_blogger_post(self.client, phone="+998907778899").status_code, 302)
        self.assertEqual(User.objects.filter(status=User.Status.ACTIVE, role=User.Role.BLOGGER).count(), 1)


class AdvertiserDuplicateTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(email="st@test.local", password="x", is_staff=True, status=User.Status.ACTIVE)
        Group.objects.get_or_create(name=REGISTRATION_REVIEWERS_GROUP)[0].user_set.add(self.staff)

    def test_blocked_inn_refused(self):
        User.objects.create_user(email=legal_entity_login(INN), password="x", role=User.Role.ADVERTISER,
                                 status=User.Status.BLOCKED)
        self.assertContains(_legal_post(self.client), "Регистрация недоступна")
        self.assertFalse(LegalEntityApplication.objects.exists())

    def test_repeat_application_marked_for_staff(self):
        _legal_post(self.client)
        _legal_post(self.client)
        first, second = LegalEntityApplication.objects.order_by("pk")
        self.assertTrue(second.has_duplicate)
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("web:admin_legal_entity_detail", args=[second.pk])), "data-duplicate-inn")
        self.assertContains(self.client.get(reverse("web:admin_legal_entities")), "data-duplicate-inn")

    def test_existing_account_marked(self):
        User.objects.create_user(email=legal_entity_login(INN), password="x", role=User.Role.ADVERTISER,
                                 status=User.Status.ACTIVE)
        _legal_post(self.client)
        self.assertTrue(LegalEntityApplication.objects.get().has_duplicate)

    def test_rejected_application_is_not_a_duplicate(self):
        LegalEntityApplication.objects.create(company_name="Старая", inn=INN, status=LegalEntityApplication.Status.REJECTED)
        _legal_post(self.client)
        self.assertFalse(LegalEntityApplication.objects.exclude(company_name="Старая").get().has_duplicate)

    def test_access_not_reissued_to_blocked_account(self):
        user = User.objects.create_user(email=legal_entity_login(INN), password="x", role=User.Role.ADVERTISER,
                                        status=User.Status.BLOCKED)
        password = user.password
        application = LegalEntityApplication.objects.create(
            company_name="ООО Т", inn=INN, status=LegalEntityApplication.Status.APPROVED, assigned_to=self.staff,
        )
        self.client.force_login(self.staff)
        self.client.post(reverse("web:admin_legal_entity_issue_access", args=[application.pk]))
        user.refresh_from_db()
        self.assertEqual((user.status, user.password), (User.Status.BLOCKED, password))
