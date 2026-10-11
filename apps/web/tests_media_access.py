"""
#56: загруженные файлы раздаются через проверку прав (а не открытым каталогом media, который на сервере вообще не
отдавался — 404), сотрудник видит всё, и его доступ к чужим документам пишется в журнал персональных данных.
"""
import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.business_queries.models import Ticket, TicketAttachment
from apps.campaigns.models import Campaign
from apps.campaigns.models import Response as CampaignResponse
from apps.campaigns.testing import deal_from_response
from apps.deals.models import ChatMessage, DealEvidence
from apps.platforms.models import Platform
from apps.profiles.models import BloggerProfile
from apps.registration.models import IPApplication, LegalEntityApplication
from apps.users.models import PersonalDataAccess, User

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

TMP = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=TMP)
class MediaAccessTest(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    def setUp(self):
        today = timezone.localdate()
        mk = User.objects.create_user
        self.adv = mk(email="adv@test.com", password="pass1234", role=User.Role.ADVERTISER, status=User.Status.ACTIVE)
        self.blogger = mk(email="bl@test.com", password="pass1234", role=User.Role.BLOGGER, status=User.Status.ACTIVE)
        self.stranger = mk(email="x@test.com", password="pass1234", role=User.Role.BLOGGER, status=User.Status.ACTIVE)
        self.staff = mk(email="st@test.com", password="pass1234", is_staff=True, status=User.Status.ACTIVE)
        platform = Platform.objects.create(
            blogger=self.blogger, social_type=Platform.SocialType.INSTAGRAM, url="https://instagram.com/media",
            subscribers=12000, price_post=Decimal("50000"), status=Platform.Status.APPROVED,
        )
        campaign = Campaign.objects.create(
            advertiser=self.adv, name="Файлы", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=Campaign.Status.ACTIVE,
            start_date=today, end_date=today + timedelta(days=30), approval_required=False,
        )
        from apps.billing.models import Wallet

        Wallet.objects.filter(user=self.adv).update(available_balance=Decimal("1000000"))
        resp = CampaignResponse.objects.create(campaign=campaign, blogger=self.blogger, platform=platform,
                                               content_type="post", proposed_price=Decimal("150000"))
        self.deal = deal_from_response(resp, self.adv, publication_date=today + timedelta(days=1))
        self.evidence = DealEvidence(deal=self.deal, kind="screenshot", uploaded_by=self.blogger)
        self.evidence.file.save("shot.png", ContentFile(PNG))
        self.ip_doc = IPApplication(user=self.blogger, document_type="patent")
        self.ip_doc.file.save("patent.png", ContentFile(PNG))
        PersonalDataAccess.objects.all().delete()

    def _get(self, user, url):
        self.client.logout()
        if user:
            self.client.force_login(user)
        return self.client.get(url)

    def test_deal_file_for_parties_staff_only(self):
        url = self.evidence.file.url
        self.assertTrue(url.startswith("/media/deal_evidence/"))
        for user in (self.blogger, self.adv):
            answer = self._get(user, url)
            self.assertEqual(answer.status_code, 200)
            self.assertEqual(answer["Content-Type"], "image/png")
            self.assertIn("no-store", answer["Cache-Control"])
        self.assertEqual(self._get(self.stranger, url).status_code, 404)
        self.assertEqual(self._get(None, url).status_code, 404)
        self.assertFalse(PersonalDataAccess.objects.exists())  # стороны своей сделки в журнал не пишутся

        self.assertEqual(self._get(self.staff, url).status_code, 200)
        entry = PersonalDataAccess.objects.get()
        self.assertEqual((entry.staff, entry.subject), (self.staff, self.blogger))
        self.assertIn(f"сделка #{self.deal.pk}", entry.action)

    def test_status_document_for_owner_and_staff(self):
        url = self.ip_doc.file.url
        self.assertEqual(self._get(self.blogger, url).status_code, 200)
        self.assertEqual(self._get(self.adv, url).status_code, 404)
        self.assertEqual(self._get(self.staff, url).status_code, 200)
        self.assertEqual(PersonalDataAccess.objects.get().subject, self.blogger)

    def test_unknown_or_traversal_is_404_even_for_staff(self):
        from django.core.files.storage import default_storage

        default_storage.save("ip_documents/orphan.png", ContentFile(PNG))
        for url in ("/media/ip_documents/orphan.png", "/media/../config/settings/base.py", "/media/secret.txt",
                    "/media/other_dir/x.png"):
            self.assertEqual(self._get(self.staff, url).status_code, 404, url)

    def test_html_in_chat_is_downloaded_not_rendered(self):
        message = ChatMessage(deal=self.deal, sender=self.blogger, text="файл")
        message.file.save("page.html", ContentFile(b"<script>alert(1)</script>"))
        answer = self._get(self.adv, message.file.url)
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["Content-Type"], "application/octet-stream")
        self.assertIn("attachment", answer["Content-Disposition"])

    def test_ticket_attachment_only_for_it_team(self):
        ticket = Ticket.objects.create(title="T", description="d", created_by=self.staff)
        attachment = TicketAttachment(ticket=ticket)
        attachment.file.save("s.png", ContentFile(PNG))
        self.assertEqual(self._get(self.staff, attachment.file.url).status_code, 404)
        self.staff.groups.add(Group.objects.get_or_create(name="IT Team")[0])
        self.assertEqual(self._get(self.staff, attachment.file.url).status_code, 200)

    def test_avatar_for_logged_in(self):
        profile, _ = BloggerProfile.objects.get_or_create(user=self.blogger)
        profile.avatar.save("a.png", ContentFile(PNG))
        self.assertEqual(self._get(None, profile.avatar.url).status_code, 404)
        self.assertEqual(self._get(self.adv, profile.avatar.url).status_code, 200)
        self.assertFalse(PersonalDataAccess.objects.exists())


class PersonalDataJournalTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(email="st@test.com", password="pass1234", is_staff=True,
                                              status=User.Status.ACTIVE)
        self.app = LegalEntityApplication.objects.create(company_name="ООО Тест", inn="123456789")
        self.client.force_login(self.staff)

    def test_card_registry_and_export_are_logged(self):
        from django.urls import reverse

        self.client.get(reverse("web:admin_legal_entity_detail", kwargs={"pk": self.app.pk}))
        self.client.get(reverse("web:admin_legal_entity_registry"))
        self.client.get(reverse("web:admin_legal_entity_registry") + "?export=xlsx")
        actions = list(PersonalDataAccess.objects.order_by("created_at").values_list("action", flat=True))
        self.assertEqual(len(actions), 3)
        self.assertIn("ИНН 123456789", actions[0])
        self.assertIn("Excel, заявок: 1", actions[2])

    def test_queues_are_logged(self):
        from django.urls import reverse

        self.client.get(reverse("web:admin_ip_applications"))
        self.client.get(reverse("web:admin_payout_requisites"))
        self.assertEqual(PersonalDataAccess.objects.count(), 2)

    def test_journal_admin_read_only_for_superuser(self):
        from django.contrib.admin.sites import site

        admin = site._registry[PersonalDataAccess]
        request = self.client.get("/").wsgi_request
        request.user = self.staff
        self.assertFalse(admin.has_view_permission(request))
        self.staff.is_superuser = True
        self.assertTrue(admin.has_view_permission(request))
        self.assertFalse(admin.has_change_permission(request))
        self.assertFalse(admin.has_delete_permission(request))
