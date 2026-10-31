"""Документы на бренд в кампании: загрузка, права на файл, запрос модератора."""
import shutil
import tempfile
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.campaigns.models import Campaign, CampaignBrandDocument
from apps.notifications.models import Notification
from apps.users.models import PersonalDataAccess, User

MEDIA = tempfile.mkdtemp()


def _pdf(name="brand.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")


@override_settings(MEDIA_ROOT=MEDIA, STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class BrandDocumentTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.adv = User.objects.create_user(email="a@test.local", password="x", role=User.Role.ADVERTISER,
                                            status=User.Status.ACTIVE)
        self.staff = User.objects.create_user(email="s@test.local", password="x", is_staff=True, status=User.Status.ACTIVE)
        self.blogger = User.objects.create_user(email="b@test.local", password="x", role=User.Role.BLOGGER,
                                                status=User.Status.ACTIVE)
        self.campaign = Campaign.objects.create(advertiser=self.adv, name="К", description="о", budget=Decimal("1000000"),
                                                fixed_price=Decimal("100000"), status=Campaign.Status.MODERATION)
        self.upload_url = reverse("web:campaign_brand_document_upload", args=[self.campaign.pk])
        self.client.force_login(self.adv)

    def _upload(self, file=None):
        return self.client.post(self.upload_url, {"file": file or _pdf()})

    def test_owner_sees_block_and_uploads(self):
        self.assertContains(self.client.get(reverse("web:campaign_detail", args=[self.campaign.pk])), "data-brand-documents")
        self._upload()
        document = CampaignBrandDocument.objects.get()
        self.assertEqual(document.campaign, self.campaign)
        self.assertTrue(document.file.name.startswith("campaign_brand_docs/"))

    def test_wrong_type_and_size_refused(self):
        self._upload(SimpleUploadedFile("x.exe", b"MZ"))
        with override_settings(BRAND_DOCUMENT_MAX_BYTES=4):
            self._upload()
        self.assertFalse(CampaignBrandDocument.objects.exists())

    def test_foreign_campaign_refused(self):
        other = User.objects.create_user(email="o@test.local", password="x", role=User.Role.ADVERTISER,
                                         status=User.Status.ACTIVE)
        self.client.force_login(other)
        self.assertEqual(self._upload().status_code, 404)

    def test_finished_campaign_refused(self):
        Campaign.objects.filter(pk=self.campaign.pk).update(status=Campaign.Status.COMPLETED)
        self._upload()
        self.assertFalse(CampaignBrandDocument.objects.exists())

    def test_delete_own(self):
        self._upload()
        document = CampaignBrandDocument.objects.get()
        self.client.post(reverse("web:campaign_brand_document_delete", args=[self.campaign.pk, document.pk]))
        self.assertFalse(CampaignBrandDocument.objects.exists())

    def test_file_rights(self):
        self._upload()
        url = CampaignBrandDocument.objects.get().file.url
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.blogger)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertTrue(PersonalDataAccess.objects.filter(staff=self.staff, subject=self.adv).exists())

    def test_blogger_does_not_see_block(self):
        Campaign.objects.filter(pk=self.campaign.pk).update(status=Campaign.Status.ACTIVE)
        self.client.force_login(self.blogger)
        self.assertNotContains(self.client.get(reverse("web:campaign_detail", args=[self.campaign.pk])), "data-brand-documents")

    def test_moderator_sees_documents(self):
        self._upload()
        self.client.force_login(self.staff)
        response = self.client.get(reverse("web:admin_campaign_detail", args=[self.campaign.pk]))
        self.assertContains(response, "data-moderator-brand")
        self.assertContains(response, "brand")

    def test_request_blocks_approval_until_upload(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("web:admin_campaign_request_brand_document", args=[self.campaign.pk]))
        self.assertTrue(Notification.objects.filter(user=self.adv, title="Нужен документ на бренд").exists())

        approve = reverse("web:admin_campaign_approve", args=[self.campaign.pk])
        self.client.post(approve)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.MODERATION)

        self.client.force_login(self.adv)
        self.assertContains(self.client.get(reverse("web:campaign_detail", args=[self.campaign.pk])), "data-brand-requested")
        self._upload()
        self.client.force_login(self.staff)
        self.client.post(approve)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.ACTIVE)

    def test_not_required_without_request(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("web:admin_campaign_approve", args=[self.campaign.pk]))
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.ACTIVE)
