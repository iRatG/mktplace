"""Рекламодатель регистрируется только с ИНН: юрлицо, ИП или самозанятый."""
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.registration.models import LegalEntityApplication
from apps.registration.services import REGISTRATION_REVIEWERS_GROUP, legal_entity_login
from apps.users.models import User

INN = "305123456"


class AdvertiserRegistrationTests(TestCase):
    def _submit(self, **extra):
        data = {"company_name": "Иванов И.И.", "inn": INN, "accept_terms": "on", **extra}
        return self.client.post(reverse("web:legal_entity_submit"), data)

    def test_register_offers_three_categories(self):
        response = self.client.get(reverse("web:register"))
        for value in ("legal_entity", "ip", "self_employed"):
            self.assertContains(response, f'value="{value}"')
        self.assertContains(response, "Компания, ИП или самозанятый")

    def test_category_saved(self):
        for category in ("ip", "self_employed"):
            self._submit(category=category)
        self.assertEqual(
            sorted(LegalEntityApplication.objects.values_list("category", flat=True)), ["ip", "self_employed"],
        )

    def test_default_is_legal_entity(self):
        self._submit()
        self.assertEqual(LegalEntityApplication.objects.get().category, "legal_entity")

    def test_unknown_category_refused(self):
        self._submit(category="individual")
        self.assertFalse(LegalEntityApplication.objects.exists())

    def test_inn_required(self):
        self.client.post(reverse("web:legal_entity_submit"), {"company_name": "Иванов", "accept_terms": "on"})
        self.assertFalse(LegalEntityApplication.objects.exists())

    def test_api_refuses_advertiser_without_inn(self):
        payload = {"email": "a@test.local", "role": "advertiser", "password": "Xy7!plmq2", "password_confirm": "Xy7!plmq2",
                   "accept_terms": True}
        response = APIClient().post("/api/v1/auth/register/", payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("role", response.json())
        self.assertFalse(User.objects.filter(email="a@test.local").exists())


class AccessIssueCategoryTests(TestCase):
    def test_category_reaches_profile_and_staff_pages(self):
        staff = User.objects.create_user(email="st@test.local", password="x", is_staff=True, status=User.Status.ACTIVE)
        Group.objects.get_or_create(name=REGISTRATION_REVIEWERS_GROUP)[0].user_set.add(staff)
        application = LegalEntityApplication.objects.create(
            category="self_employed", company_name="Иванов И.И.", inn=INN, assigned_to=staff,
            status=LegalEntityApplication.Status.APPROVED, ddocs_status=LegalEntityApplication.DdocsStatus.SIGNED,
        )
        self.client.force_login(staff)
        self.assertContains(self.client.get(reverse("web:admin_legal_entity_detail", args=[application.pk])), "Самозанятый")
        self.assertContains(self.client.get(reverse("web:admin_legal_entity_registry")), "Самозанятый")

        self.client.post(reverse("web:admin_legal_entity_issue_access", args=[application.pk]))
        profile = User.objects.get(email=legal_entity_login(INN)).advertiser_profile
        self.assertEqual((profile.category, profile.inn), ("self_employed", INN))
