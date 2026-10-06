"""
QA camp_test_2 (06.10.2026): «200000 UZS» без разрядов, новое в «Требует действия» в конце списка,
ИНН из 8 цифр в реестре юрлиц. Одно правило на каждое: суммы — только format_money/|money,
«Требует действия» — от новых к старым, ИНН — validate_inn везде.
"""
import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import Group
from django.forms import modelform_factory
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.billing.formatting import format_money
from apps.campaigns.models import Campaign
from apps.deals.models import Deal
from apps.notifications.models import Notification
from apps.notifications.service import NotificationService
from apps.platforms.models import Platform
from apps.registration.models import LegalEntityApplication
from apps.registration.services import REGISTRATION_REVIEWERS_GROUP
from apps.users.models import User

TEMPLATES_DIR = Path(settings.BASE_DIR) / "templates"


class FormatMoneyTest(SimpleTestCase):
    def test_values(self):
        self.assertEqual(format_money(1500000), "1 500 000")
        self.assertEqual(format_money(Decimal("200000.00")), "200 000")
        self.assertEqual(format_money(Decimal("-200000")), "-200 000")
        self.assertEqual(format_money("99.5"), "99.50")
        self.assertEqual(format_money("1 500 000"), "1 500 000")
        self.assertEqual(format_money(None), "")


class TemplatesUseMoneyGuardTest(SimpleTestCase):
    """Страж: суммы и счётчики в шаблонах — только через |money, а не floatformat:0 или сырым числом."""

    AMOUNT = r"(amount|price\w*|budget|\w*balance|on_withdrawal|cpa_rate|min_withdrawal|min_deposit|subscribers|total)"

    def _templates(self):
        return sorted(TEMPLATES_DIR.rglob("*.html"))

    def test_no_floatformat_zero_without_money(self):
        offenders = []
        for path in self._templates():
            for m in re.finditer(r"\{\{[^}]*\|floatformat:0(?![^}]*\|money)[^}]*\}\}", path.read_text(encoding="utf-8")):
                offenders.append(f"{path.relative_to(TEMPLATES_DIR)}: {m.group(0)}")
        self.assertEqual(offenders, [], "Используйте |money для сумм и счётчиков")

    def test_no_raw_amounts_before_currency(self):
        offenders = []
        pattern = re.compile(r"\{\{ *[\w.]*" + self.AMOUNT + r" *\}\} *\{\{ *currency\.symbol")
        for path in self._templates():
            for m in pattern.finditer(path.read_text(encoding="utf-8")):
                offenders.append(f"{path.relative_to(TEMPLATES_DIR)}: {m.group(0)}")
        self.assertEqual(offenders, [], "Сумма перед валютой без |money")


def _user(email, role=User.Role.ADVERTISER, is_staff=False):
    return User.objects.create_user(
        email=email, password="pass1234", role=role, status=User.Status.ACTIVE, is_staff=is_staff,
    )


class AmountsOnPagesTest(TestCase):
    def setUp(self):
        self.adv = _user("adv@test.com")
        self.blogger = _user("bl@test.com", User.Role.BLOGGER)
        self.campaign = Campaign.objects.create(
            advertiser=self.adv, name="К", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=Campaign.Status.ACTIVE,
        )
        self.platform = Platform.objects.create(
            blogger=self.blogger, social_type=Platform.SocialType.INSTAGRAM, url="https://instagram.com/x",
            subscribers=12000, price_post=Decimal("50000"), status=Platform.Status.APPROVED,
        )

    def _deal(self, **extra):
        fields = dict(campaign=self.campaign, blogger=self.blogger, advertiser=self.adv,
                      platform=self.platform, amount=Decimal("200000"), status=Deal.Status.IN_PROGRESS)
        fields.update(extra)
        return Deal.objects.create(**fields)

    def test_deal_page_amount_with_separators(self):
        deal = self._deal()
        self.client.force_login(self.adv)
        r = self.client.get(reverse("web:deal_detail", kwargs={"pk": deal.pk}))
        self.assertContains(r, "200 000")
        self.assertNotContains(r, "200000 ")

    def test_campaign_list_budget_with_separators(self):
        self.client.force_login(self.adv)
        self.assertContains(self.client.get(reverse("web:campaign_list")), "1 500 000")

    def test_withdrawal_notification_uses_spaces(self):
        NotificationService.notify_withdrawal_approved(self.blogger, Decimal("1500000"))
        body = Notification.objects.get(user=self.blogger).body
        self.assertIn("1 500 000", body)
        self.assertNotIn("1,500,000", body)

    def test_needs_action_newest_first(self):
        old = self._deal(status=Deal.Status.ON_APPROVAL)
        new = self._deal(status=Deal.Status.ON_APPROVAL)
        Deal.objects.filter(pk=old.pk).update(updated_at=timezone.now() - timedelta(days=2))
        self.client.force_login(self.adv)
        deals = list(self.client.get(reverse("web:advertiser_dashboard")).context["action_deals"])
        self.assertEqual([d.pk for d in deals], [new.pk, old.pk])


class InnValidationTest(TestCase):
    def test_model_validator_used_by_admin_forms(self):
        Form = modelform_factory(LegalEntityApplication, fields=["company_name", "inn"])
        self.assertFalse(Form({"company_name": "ООО", "inn": "89562126"}).is_valid())
        self.assertTrue(Form({"company_name": "ООО", "inn": "895621260"}).is_valid())

    def test_issue_access_refuses_invalid_inn(self):
        reviewer = _user("rev@test.com", is_staff=True)
        reviewer.groups.add(Group.objects.get_or_create(name=REGISTRATION_REVIEWERS_GROUP)[0])
        app = LegalEntityApplication.objects.create(
            company_name="Старая заявка", inn="89562126", assigned_to=reviewer,
            status=LegalEntityApplication.Status.APPROVED,
        )
        self.client.force_login(reviewer)
        r = self.client.post(reverse("web:admin_legal_entity_issue_access", args=[app.pk]))
        self.assertRedirects(r, reverse("web:admin_legal_entity_detail", args=[app.pk]), fetch_redirect_response=False)
        app.refresh_from_db()
        self.assertIsNone(app.user)
        self.assertNotEqual(app.ddocs_status, LegalEntityApplication.DdocsStatus.ACCESS_ISSUED)
        self.assertFalse(User.objects.filter(email__startswith="legal.89562126").exists())
