"""
QA camp_test_3 (07.10.2026), шаги 1.2 и 5.1: кнопок −/+ не было видно.

Статика отдавалась под постоянными именами с кэшем на год, и браузер держал старый number-input.js. В production
теперь имена с хэшем содержимого; цена отклика — на общем шаблоне числового поля с кнопками.
"""
import ast
from pathlib import Path
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.campaigns.models import Campaign
from apps.platforms.models import Platform
from apps.users.models import User


class ProductionStaticStorageTest(TestCase):
    def test_production_uses_hashed_manifest_storage(self):
        # Модуль production не импортируем: в локальном образе нет sentry_sdk. Читаем присваивание STORAGES.
        source = (Path(__file__).resolve().parents[2] / "config" / "settings" / "production.py").read_text("utf-8")
        storages = next(
            ast.literal_eval(node.value) for node in ast.parse(source).body
            if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "STORAGES" for t in node.targets)
        )
        self.assertEqual(
            storages["staticfiles"]["BACKEND"], "whitenoise.storage.CompressedManifestStaticFilesStorage",
        )
        self.assertEqual(storages["default"]["BACKEND"], "django.core.files.storage.FileSystemStorage")


class ResponsePriceButtonsTest(TestCase):
    def setUp(self):
        adv = User.objects.create_user(
            email="adv@test.com", password="pass1234", role=User.Role.ADVERTISER, status=User.Status.ACTIVE,
        )
        self.blogger = User.objects.create_user(
            email="bl@test.com", password="pass1234", role=User.Role.BLOGGER, status=User.Status.ACTIVE,
        )
        self.campaign = Campaign.objects.create(
            advertiser=adv, name="Кампания", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=Campaign.Status.ACTIVE,
        )
        Platform.objects.create(
            blogger=self.blogger, social_type=Platform.SocialType.INSTAGRAM, url="https://instagram.com/b",
            subscribers=12000, price_post=Decimal("50000"), status=Platform.Status.APPROVED,
        )

    def test_response_price_has_step_buttons(self):
        self.client.force_login(self.blogger)
        html = self.client.get(reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk})).content.decode()
        start = html.index('name="proposed_price"')
        group = html[html.rindex("data-number-group", 0, start):html.index("</div>", start)]
        self.assertIn('data-step="10000"', group)
        self.assertIn('aria-label="Увеличить на 10000"', html[start:start + 800])
        self.assertIn('aria-label="Уменьшить на 10000"', group)

    def test_response_price_with_spaces_still_saved(self):
        self.client.force_login(self.blogger)
        platform = Platform.objects.get(blogger=self.blogger)
        self.client.post(reverse("web:campaign_respond", kwargs={"pk": self.campaign.pk}), {
            "platform": platform.pk, "content_type": "post", "proposed_price": "150 000",
        })
        self.assertEqual(self.campaign.responses.get().proposed_price, Decimal("150000"))
