"""
#42: поля карточки кампании и оферты (задание, приёмка, права), обязательные для модерации поля, разрешительный
документ для регулируемой категории; условия оферты видны исполнителю и сторонам сделки.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.billing.models import Wallet
from apps.campaigns.models import Campaign
from apps.campaigns.models import Response as CampaignResponse
from apps.campaigns.services import accept_response
from apps.campaigns.testing import CARD_FIELDS, publication_day
from apps.campaigns.validation import moderation_error
from apps.platforms.models import Category, PermitDocument, Platform
from apps.users.models import User
from apps.web.campaign_proposals import describe_terms
from apps.web.forms import CampaignForm


def _user(email, role=User.Role.ADVERTISER, **extra):
    return User.objects.create_user(email=email, password="pass1234", role=role, status=User.Status.ACTIVE, **extra)


class CardFieldsTest(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.adv = _user("adv@test.com", is_demo=True)  # демо — без проверки подтверждения аккаунта
        self.staff = _user("staff@test.com", is_staff=True)
        self.campaign = Campaign.objects.create(
            advertiser=self.adv, name="Кампания", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=Campaign.Status.DRAFT,
            start_date=self.today, end_date=self.today + timedelta(days=40),
        )

    def _fill(self, **extra):
        Campaign.objects.filter(pk=self.campaign.pk).update(**{**CARD_FIELDS, **extra})
        self.campaign.refresh_from_db()

    # ── Модерация ────────────────────────────────────────────────────────────

    def test_draft_without_card_is_saved_but_not_submitted(self):
        error = moderation_error(self.campaign)
        self.assertIn("критерии приёмки", error)
        self.assertIn("обязательные предупреждения и раскрытия", error)
        self.client.force_login(self.adv)
        self.client.post(reverse("web:campaign_submit", kwargs={"pk": self.campaign.pk}))
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.DRAFT)

        api = APIClient()
        api.force_authenticate(self.adv)
        r = api.post(f"/api/v1/campaigns/{self.campaign.pk}/submit_for_moderation/")
        self.assertEqual(r.status_code, 400)

        self._fill()
        self.assertIsNone(moderation_error(self.campaign))
        self.client.post(reverse("web:campaign_submit", kwargs={"pk": self.campaign.pk}))
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.MODERATION)

    def test_paused_edit_requires_card_fields(self):
        self._fill(status=Campaign.Status.PAUSED)
        form = CampaignForm(instance=self.campaign, data={
            "name": "Кампания", "payment_type": "fixed", "fixed_price": "150000", "budget": "1500000",
            "start_date": self.today.isoformat(), "end_date": (self.today + timedelta(days=40)).isoformat(),
            "min_subscribers": "0", "max_bloggers": "0", "subject": "Крем", "description": "Пост",
            "content_types": ["post"], "allowed_socials": ["instagram"],
        })
        self.assertFalse(form.is_valid())
        for name in ("acceptance_criteria", "disclosures", "content_restrictions"):
            self.assertIn(name, form.errors)

    # ── Разрешительный документ ──────────────────────────────────────────────

    def _regulated(self):
        cat = Category.objects.create(name="Лекарства", slug="med", is_regulated=True,
                                      regulated_doc_hint="лицензия на фармдеятельность")
        self._fill(category=cat)
        return cat

    def _permit(self, cat, status=PermitDocument.Status.APPROVED, expires_at=None):
        return PermitDocument.objects.create(
            user=self.adv, category=cat, doc_type=PermitDocument.DocType.LICENSE, doc_number="1",
            issued_by="Минздрав", issued_date=self.today - timedelta(days=100), expires_at=expires_at,
            file=SimpleUploadedFile("l.pdf", b"%PDF"), status=status,
        )

    def test_regulated_category_needs_valid_permit(self):
        cat = self._regulated()
        self.assertIn("лицензия на фармдеятельность", moderation_error(self.campaign))
        self._permit(cat, status=PermitDocument.Status.PENDING)
        self.assertIsNotNone(moderation_error(self.campaign))
        self._permit(cat, expires_at=self.today - timedelta(days=1))
        self.assertIsNotNone(moderation_error(self.campaign))
        self._permit(cat, expires_at=self.today + timedelta(days=30))
        self.assertIsNone(moderation_error(self.campaign))

    def test_moderator_cannot_approve_without_permit(self):
        self._regulated()
        Campaign.objects.filter(pk=self.campaign.pk).update(status=Campaign.Status.MODERATION)
        self.client.force_login(self.staff)
        page = self.client.get(reverse("web:admin_campaign_detail", kwargs={"pk": self.campaign.pk}))
        self.assertContains(page, "data-permit-error")
        self.client.post(reverse("web:admin_campaign_approve", kwargs={"pk": self.campaign.pk}))
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.MODERATION)

    # ── Форма и API ──────────────────────────────────────────────────────────

    def test_form_defaults_and_save(self):
        self.client.force_login(self.adv)
        data = {"name": "Новая", "payment_type": "fixed", "fixed_price": "150000", "budget": "1500000",
                "min_subscribers": "0", "max_bloggers": "0", **CARD_FIELDS,
                "key_message": "Защита от солнца", "evidence_required": ["screenshot", "statistics"]}
        self.client.post(reverse("web:campaign_create"), data)
        c = Campaign.objects.get(name="Новая")
        self.assertEqual((c.content_units, c.min_retention_days, c.rights_owner, c.reuse_allowed),
                         (1, 3, Campaign.RightsOwner.BLOGGER, False))
        self.assertEqual(c.key_message, "Защита от солнца")
        self.assertEqual(c.evidence_required, ["screenshot", "statistics"])

    def test_api_evidence_validated(self):
        api = APIClient()
        api.force_authenticate(self.adv)
        payload = {"name": "API", "payment_type": "fixed", "fixed_price": "150000", "budget": "1500000",
                   **CARD_FIELDS, "evidence_required": ["selfie"]}
        r = api.post("/api/v1/campaigns/", payload, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("evidence_required", r.json())
        payload["evidence_required"] = ["retention"]
        payload["min_retention_days"] = 7
        r = api.post("/api/v1/campaigns/", payload, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Campaign.objects.get(name="API").min_retention_days, 7)

    # ── Условия видны исполнителю ────────────────────────────────────────────

    def test_terms_in_offer_deal_and_campaign_page(self):
        self._fill(status=Campaign.Status.ACTIVE, key_message="Защита от солнца", min_retention_days=7,
                   rights_owner=Campaign.RightsOwner.ADVERTISER, reuse_allowed=True)
        Wallet.objects.filter(user=self.adv).update(available_balance=Decimal("1000000"))
        blogger = _user("bl@test.com", User.Role.BLOGGER)
        platform = Platform.objects.create(
            blogger=blogger, social_type=Platform.SocialType.INSTAGRAM, url="https://instagram.com/cf",
            subscribers=12000, price_post=Decimal("50000"), status=Platform.Status.APPROVED,
        )
        resp = CampaignResponse.objects.create(campaign=self.campaign, blogger=blogger, platform=platform,
                                               content_type="post", proposed_price=Decimal("150000"))
        offer = accept_response(resp.pk, self.adv, publication_day(self.campaign))
        rows = dict(describe_terms(offer.terms))
        self.assertEqual(rows["Основное сообщение"], "Защита от солнца")
        self.assertEqual(rows["Мин. срок сохранения публикации, дней"], "7")
        self.assertEqual(rows["Исключительные права на контент"], "Рекламодателю (после оплаты)")
        self.assertEqual(rows["Право на репост, таргет, адаптацию"], "да")

        self.client.force_login(blogger)
        dashboard = self.client.get(reverse("web:blogger_dashboard")).content.decode()
        self.assertIn("data-offer-terms", dashboard)
        self.assertIn("Защита от солнца", dashboard)
        page = self.client.get(reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk})).content.decode()
        self.assertIn("data-card-terms", page)

        # правка кампании после оферты не меняет условия сделки
        Campaign.objects.filter(pk=self.campaign.pk).update(key_message="Другое")
        from apps.campaigns.services import accept_direct_offer

        deal = accept_direct_offer(offer.pk, blogger)
        page = self.client.get(reverse("web:deal_detail", kwargs={"pk": deal.pk})).content.decode()
        self.assertIn("data-offer-terms", page)
        self.assertIn("Защита от солнца", page)
        self.assertNotIn("Другое", page)

    def test_old_offer_terms_without_new_fields_shown_partially(self):
        rows = describe_terms({"subject": "Крем", "approval_required": "True"})
        self.assertEqual(rows, [("Что рекламируем", "Крем"), ("Согласовать материал перед публикацией", "да")])
