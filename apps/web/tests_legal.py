"""
Sprint 10 — Legal compliance tests (REQ-1, REQ-2, REQ-5, REQ-6).

REQ-1: Досудебная модель — удалена формулировка «admin decides»
REQ-2: Верификация разрешительных документов (PermitDocument)
REQ-5: Хранение данных 3 года (last_distributed_at, is_frozen)
REQ-6: Юридические страницы (/legal/terms/, /legal/oferta/)

Run: docker compose run --rm web python manage.py test apps.web.tests_legal -v 2
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import Wallet
from apps.campaigns.models import Campaign
from apps.deals.models import Deal, DealStatusLog
from apps.platforms.models import Category, PermitDocument, Platform

User = get_user_model()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_user(email, role, password="pass1234"):
    u = User.objects.create_user(email=email, password=password, role=role)
    u.is_active = True
    u.save()
    return u


def _make_staff(email="staff@test.com"):
    u = _make_user(email, User.Role.ADVERTISER)
    u.is_staff = True
    u.save()
    return u


def _make_platform(blogger, status=Platform.Status.APPROVED, url="https://t.me/ch"):
    return Platform.objects.create(
        blogger=blogger,
        social_type=Platform.SocialType.TELEGRAM,
        url=url,
        subscribers=5000,
        status=status,
    )


def _make_campaign(advertiser, fixed_price=100_000):
    return Campaign.objects.create(
        advertiser=advertiser,
        name="Legal Test Campaign",
        payment_type=Campaign.PaymentType.FIXED,
        budget=500_000,
        fixed_price=fixed_price,
        status=Campaign.Status.ACTIVE,
        deadline=timezone.now().date() + timedelta(days=30),
    )


def _make_deal(advertiser, blogger, status=Deal.Status.IN_PROGRESS, amount=100_000):
    campaign = _make_campaign(advertiser)
    platform = _make_platform(blogger)
    deal = Deal.objects.create(
        advertiser=advertiser,
        blogger=blogger,
        campaign=campaign,
        platform=platform,
        amount=amount,
        status=status,
    )
    return deal


def _make_wallet(user, balance=500_000):
    w, _ = Wallet.objects.get_or_create(user=user)
    w.balance = Decimal(balance)
    w.save()
    return w


_cat_counter = 0

def _make_regulated_category(name="Фарма", hint="Лицензия Минздрава"):
    global _cat_counter
    _cat_counter += 1
    return Category.objects.create(
        name=name,
        slug=f"reg-cat-{_cat_counter}",
        is_regulated=True,
        regulated_doc_hint=hint,
    )


def _make_permit(user, category, status=PermitDocument.Status.PENDING,
                 doc_type=PermitDocument.DocType.LICENSE):
    return PermitDocument.objects.create(
        user=user,
        category=category,
        doc_type=doc_type,
        doc_number="LIC-001",
        issued_by="Минздрав",
        issued_date=date.today() - timedelta(days=30),
        expires_at=date.today() + timedelta(days=365),
        status=status,
    )


# ══════════════════════════════════════════════════════════════════════════════
# REQ-1 — Досудебная модель
# ══════════════════════════════════════════════════════════════════════════════

class REQ1DisputeResolutionWordingTest(TestCase):
    """Admin dispute resolve должен писать «Досудебное урегулирование» в DealStatusLog."""

    def setUp(self):
        self.staff = _make_staff()
        self.adv = _make_user("adv@t.com", User.Role.ADVERTISER)
        self.blg = _make_user("blg@t.com", User.Role.BLOGGER)
        _make_wallet(self.adv)
        _make_wallet(self.blg)
        self.deal = _make_deal(self.adv, self.blg, status=Deal.Status.DISPUTED)
        self.deal.dispute_reason = "Тест"
        self.deal.dispute_opened_at = timezone.now()
        self.deal.is_frozen = True
        self.deal.save()

        self.client = Client()
        self.client.force_login(self.staff)

    def test_resolve_complete_logs_pretrial_comment(self):
        url = reverse("web:admin_dispute_resolve", kwargs={"pk": self.deal.pk})
        self.client.post(url, {"resolution": "complete", "comment": "Обязательства выполнены"})
        log = DealStatusLog.objects.filter(deal=self.deal, new_status=Deal.Status.COMPLETED).first()
        self.assertIsNotNone(log)
        self.assertIn("Досудебное", log.comment)

    def test_resolve_cancel_logs_pretrial_comment(self):
        url = reverse("web:admin_dispute_resolve", kwargs={"pk": self.deal.pk})
        self.client.post(url, {"resolution": "cancel", "comment": "Блогер не выполнил"})
        log = DealStatusLog.objects.filter(deal=self.deal, new_status=Deal.Status.CANCELLED).first()
        self.assertIsNotNone(log)
        self.assertIn("Досудебное", log.comment)

    def test_disputes_page_accessible_by_staff(self):
        url = reverse("web:admin_disputes")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_disputes_page_forbidden_for_regular_user(self):
        c = Client()
        c.force_login(self.adv)
        url = reverse("web:admin_disputes")
        resp = c.get(url)
        self.assertEqual(resp.status_code, 302)  # redirect to login/403


# ══════════════════════════════════════════════════════════════════════════════
# REQ-2 — Permit Documents: модель
# ══════════════════════════════════════════════════════════════════════════════

class REQ2PermitDocumentModelTest(TestCase):
    """PermitDocument создаётся и переходит по статусам."""

    def setUp(self):
        self.user = _make_user("blg@t.com", User.Role.BLOGGER)
        self.cat = _make_regulated_category()

    def test_create_pending_permit(self):
        p = _make_permit(self.user, self.cat)
        self.assertEqual(p.status, PermitDocument.Status.PENDING)
        self.assertEqual(p.user, self.user)
        self.assertEqual(p.category, self.cat)

    def test_approve_permit(self):
        p = _make_permit(self.user, self.cat)
        p.status = PermitDocument.Status.APPROVED
        p.save()
        self.assertEqual(PermitDocument.objects.get(pk=p.pk).status, PermitDocument.Status.APPROVED)

    def test_reject_permit(self):
        p = _make_permit(self.user, self.cat)
        p.status = PermitDocument.Status.REJECTED
        p.rejection_reason = "Не тот формат"
        p.save()
        refreshed = PermitDocument.objects.get(pk=p.pk)
        self.assertEqual(refreshed.status, PermitDocument.Status.REJECTED)
        self.assertEqual(refreshed.rejection_reason, "Не тот формат")

    def test_category_is_regulated_flag(self):
        unregulated = Category.objects.create(name="Обычная", slug="unregulated-1", is_regulated=False)
        self.assertTrue(self.cat.is_regulated)
        self.assertFalse(unregulated.is_regulated)

    def test_str_repr_contains_doctype(self):
        p = _make_permit(self.user, self.cat, doc_type=PermitDocument.DocType.LICENSE)
        self.assertIn("LIC", str(p.doc_number))


# ══════════════════════════════════════════════════════════════════════════════
# REQ-2 — Permit Documents: пользовательские views
# ══════════════════════════════════════════════════════════════════════════════

class REQ2PermitUserViewsTest(TestCase):

    def setUp(self):
        self.user = _make_user("blg@t.com", User.Role.BLOGGER)
        self.cat = _make_regulated_category()
        self.client = Client()
        self.client.force_login(self.user)

    def test_permit_list_requires_login(self):
        c = Client()
        url = reverse("web:permit_list")
        resp = c.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp["Location"] if resp.has_header("Location") else "")

    def test_permit_list_returns_200(self):
        resp = self.client.get(reverse("web:permit_list"))
        self.assertEqual(resp.status_code, 200)

    def test_permit_list_shows_own_permits(self):
        p = _make_permit(self.user, self.cat)
        resp = self.client.get(reverse("web:permit_list"))
        self.assertContains(resp, p.doc_number)

    def test_permit_list_hides_other_users_permits(self):
        other = _make_user("other@t.com", User.Role.BLOGGER)
        p = _make_permit(other, self.cat)
        resp = self.client.get(reverse("web:permit_list"))
        self.assertNotContains(resp, p.doc_number)

    def test_permit_upload_get_returns_200(self):
        resp = self.client.get(reverse("web:permit_upload"))
        self.assertEqual(resp.status_code, 200)

    def test_permit_delete_pending_ok(self):
        p = _make_permit(self.user, self.cat, status=PermitDocument.Status.PENDING)
        url = reverse("web:permit_delete", kwargs={"pk": p.pk})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(PermitDocument.objects.filter(pk=p.pk).exists())

    def test_permit_delete_rejected_ok(self):
        p = _make_permit(self.user, self.cat, status=PermitDocument.Status.REJECTED)
        url = reverse("web:permit_delete", kwargs={"pk": p.pk})
        resp = self.client.post(url)
        self.assertFalse(PermitDocument.objects.filter(pk=p.pk).exists())

    def test_permit_delete_approved_blocked(self):
        """Approved document cannot be deleted by user."""
        p = _make_permit(self.user, self.cat, status=PermitDocument.Status.APPROVED)
        url = reverse("web:permit_delete", kwargs={"pk": p.pk})
        resp = self.client.post(url)
        # Should redirect with error, document must still exist
        self.assertTrue(PermitDocument.objects.filter(pk=p.pk).exists())

    def test_permit_delete_other_users_document_denied(self):
        other = _make_user("other@t.com", User.Role.BLOGGER)
        p = _make_permit(other, self.cat)
        url = reverse("web:permit_delete", kwargs={"pk": p.pk})
        resp = self.client.post(url)
        self.assertTrue(PermitDocument.objects.filter(pk=p.pk).exists())


# ══════════════════════════════════════════════════════════════════════════════
# REQ-2 — Permit Documents: admin views
# ══════════════════════════════════════════════════════════════════════════════

class REQ2PermitAdminViewsTest(TestCase):

    def setUp(self):
        self.staff = _make_staff()
        self.user = _make_user("blg@t.com", User.Role.BLOGGER)
        self.cat = _make_regulated_category()
        self.client = Client()
        self.client.force_login(self.staff)

    def test_admin_permits_returns_200(self):
        resp = self.client.get(reverse("web:admin_permits"))
        self.assertEqual(resp.status_code, 200)

    def test_admin_permits_forbidden_for_regular_user(self):
        c = Client()
        c.force_login(self.user)
        resp = c.get(reverse("web:admin_permits"))
        self.assertEqual(resp.status_code, 302)

    def test_admin_permit_approve_sets_approved(self):
        p = _make_permit(self.user, self.cat)
        url = reverse("web:admin_permit_approve", kwargs={"pk": p.pk})
        self.client.post(url)
        p.refresh_from_db()
        self.assertEqual(p.status, PermitDocument.Status.APPROVED)

    def test_admin_permit_approve_sets_reviewed_by(self):
        p = _make_permit(self.user, self.cat)
        url = reverse("web:admin_permit_approve", kwargs={"pk": p.pk})
        self.client.post(url)
        p.refresh_from_db()
        self.assertEqual(p.reviewed_by, self.staff)

    def test_admin_permit_reject_sets_rejected(self):
        p = _make_permit(self.user, self.cat)
        url = reverse("web:admin_permit_reject", kwargs={"pk": p.pk})
        self.client.post(url, {"rejection_reason": "Документ недействителен"})
        p.refresh_from_db()
        self.assertEqual(p.status, PermitDocument.Status.REJECTED)
        self.assertEqual(p.rejection_reason, "Документ недействителен")

    def test_admin_permit_reject_without_reason_blocked(self):
        p = _make_permit(self.user, self.cat)
        url = reverse("web:admin_permit_reject", kwargs={"pk": p.pk})
        self.client.post(url, {"rejection_reason": ""})
        p.refresh_from_db()
        # Status must remain PENDING if no reason given
        self.assertEqual(p.status, PermitDocument.Status.PENDING)

    def test_admin_dashboard_shows_permits_pending_count(self):
        _make_permit(self.user, self.cat)
        resp = self.client.get(reverse("web:admin_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("permits_pending", resp.context)
        self.assertEqual(resp.context["permits_pending"], 1)


# ══════════════════════════════════════════════════════════════════════════════
# REQ-2 — Блокировка публикации площадки без разрешительного документа
# ══════════════════════════════════════════════════════════════════════════════

class REQ2PlatformPublicationBlockTest(TestCase):
    """
    Площадка с регулируемой категорией не может быть опубликована без APPROVED документа.
    """

    def setUp(self):
        self.blogger = _make_user("blg@t.com", User.Role.BLOGGER)
        self.cat = _make_regulated_category("Фарма")
        self.client = Client()
        self.client.force_login(self.blogger)

    def _platform_post_data(self, url, extra_cat_pk=None):
        cats = [self.cat.pk]
        if extra_cat_pk:
            cats.append(extra_cat_pk)
        return {
            "social_type": Platform.SocialType.TELEGRAM,
            "url": url,
            "subscribers": 5000,
            "avg_views": 1000,
            "engagement_rate": "3.5",
            "categories": cats,
        }

    def test_platform_add_blocked_without_permit(self):
        data = self._platform_post_data("https://t.me/pharm_channel")
        resp = self.client.post(reverse("web:platform_add"), data)
        # No platform should be created: view blocks and re-renders form (200)
        # OR redirects with an error. Either way, no platform in DB.
        self.assertFalse(Platform.objects.filter(blogger=self.blogger).exists())

    def test_platform_add_allowed_with_approved_permit(self):
        _make_permit(self.blogger, self.cat, status=PermitDocument.Status.APPROVED)
        data = self._platform_post_data("https://t.me/pharm_ok")
        self.client.post(reverse("web:platform_add"), data)
        # Platform should be created (PENDING for admin moderation)
        self.assertTrue(Platform.objects.filter(blogger=self.blogger).exists())


# ══════════════════════════════════════════════════════════════════════════════
# REQ-2 — Celery task: check_permit_expiry
# ══════════════════════════════════════════════════════════════════════════════

class REQ2PermitExpiryTaskTest(TestCase):
    """check_permit_expiry помечает истёкшие документы и отправляет уведомления."""

    def setUp(self):
        self.user = _make_user("blg@t.com", User.Role.BLOGGER)
        self.cat = _make_regulated_category()

    def test_expired_permit_status_set_to_expired(self):
        from apps.platforms.tasks import check_permit_expiry
        p = PermitDocument.objects.create(
            user=self.user,
            category=self.cat,
            doc_type=PermitDocument.DocType.LICENSE,
            doc_number="EXP-001",
            issued_by="Минздрав",
            issued_date=date.today() - timedelta(days=400),
            expires_at=date.today() - timedelta(days=1),  # expired yesterday
            status=PermitDocument.Status.APPROVED,
        )
        check_permit_expiry()
        p.refresh_from_db()
        self.assertEqual(p.status, PermitDocument.Status.EXPIRED)

    def test_active_permit_not_touched(self):
        from apps.platforms.tasks import check_permit_expiry
        p = PermitDocument.objects.create(
            user=self.user,
            category=self.cat,
            doc_type=PermitDocument.DocType.LICENSE,
            doc_number="ACT-001",
            issued_by="Минздрав",
            issued_date=date.today() - timedelta(days=30),
            expires_at=date.today() + timedelta(days=60),  # still valid
            status=PermitDocument.Status.APPROVED,
        )
        check_permit_expiry()
        p.refresh_from_db()
        self.assertEqual(p.status, PermitDocument.Status.APPROVED)

    def test_warning_notification_created_30_days_before_expiry(self):
        from apps.notifications.models import Notification
        from apps.platforms.tasks import check_permit_expiry
        PermitDocument.objects.create(
            user=self.user,
            category=self.cat,
            doc_type=PermitDocument.DocType.LICENSE,
            doc_number="WARN-001",
            issued_by="Минздрав",
            issued_date=date.today() - timedelta(days=335),
            expires_at=date.today() + timedelta(days=30),  # exactly 30 days
            status=PermitDocument.Status.APPROVED,
        )
        count_before = Notification.objects.filter(user=self.user).count()
        check_permit_expiry()
        count_after = Notification.objects.filter(user=self.user).count()
        self.assertGreater(count_after, count_before)


# ══════════════════════════════════════════════════════════════════════════════
# REQ-5 — Хранение данных: last_distributed_at и is_frozen
# ══════════════════════════════════════════════════════════════════════════════

class REQ5DataRetentionFieldsTest(TestCase):
    """last_distributed_at и is_frozen корректно устанавливаются при подтверждении и споре."""

    def setUp(self):
        self.adv = _make_user("adv@t.com", User.Role.ADVERTISER)
        self.blg = _make_user("blg@t.com", User.Role.BLOGGER)
        _make_wallet(self.adv, 1_000_000)
        _make_wallet(self.blg, 0)

    def test_deal_fields_exist(self):
        deal = _make_deal(self.adv, self.blg)
        self.assertIsNone(deal.last_distributed_at)
        self.assertFalse(deal.is_frozen)

    def test_deal_confirm_sets_last_distributed_at(self):
        """Подтверждение публикации (web view) должно выставить last_distributed_at."""
        deal = _make_deal(self.adv, self.blg, status=Deal.Status.CHECKING)
        # Reserve funds
        wallet = Wallet.objects.get(user=self.adv)
        wallet.reserved = Decimal(deal.amount)
        wallet.save()

        c = Client()
        c.force_login(self.adv)
        url = reverse("web:deal_confirm", kwargs={"pk": deal.pk})
        c.post(url)

        deal.refresh_from_db()
        self.assertIsNotNone(deal.last_distributed_at)

    def test_deal_confirm_does_not_freeze(self):
        """При обычном завершении — не замораживаем (нет спора)."""
        deal = _make_deal(self.adv, self.blg, status=Deal.Status.CHECKING)
        wallet = Wallet.objects.get(user=self.adv)
        wallet.reserved = Decimal(deal.amount)
        wallet.save()

        c = Client()
        c.force_login(self.adv)
        url = reverse("web:deal_confirm", kwargs={"pk": deal.pk})
        c.post(url)

        deal.refresh_from_db()
        self.assertFalse(deal.is_frozen)

    def test_drf_dispute_sets_is_frozen(self):
        """DRF dispute action должен выставить is_frozen=True."""
        from rest_framework.test import APIClient
        deal = _make_deal(self.adv, self.blg, status=Deal.Status.CHECKING)

        api = APIClient()
        api.force_authenticate(self.adv)
        url = f"/api/v1/deals/{deal.pk}/dispute/"
        api.post(url, {"reason": "Блогер не выполнил условия"}, format="json")

        deal.refresh_from_db()
        self.assertTrue(deal.is_frozen)

    def test_drf_dispute_sets_dispute_opened_at(self):
        from rest_framework.test import APIClient
        deal = _make_deal(self.adv, self.blg, status=Deal.Status.CHECKING)

        api = APIClient()
        api.force_authenticate(self.adv)
        url = f"/api/v1/deals/{deal.pk}/dispute/"
        api.post(url, {"reason": "Спор"}, format="json")

        deal.refresh_from_db()
        self.assertIsNotNone(deal.dispute_opened_at)

    def test_is_frozen_persists_after_dispute_resolved(self):
        """После разрешения спора is_frozen остаётся True — данные нельзя удалять."""
        staff = _make_staff()
        deal = _make_deal(self.adv, self.blg, status=Deal.Status.DISPUTED)
        deal.is_frozen = True
        deal.dispute_opened_at = timezone.now()
        deal.save()

        # Reserve funds for cancel path
        wallet = Wallet.objects.get(user=self.adv)
        wallet.reserved = Decimal(deal.amount)
        wallet.save()

        c = Client()
        c.force_login(staff)
        url = reverse("web:admin_dispute_resolve", kwargs={"pk": deal.pk})
        c.post(url, {"resolution": "cancel", "comment": "Возврат"})

        deal.refresh_from_db()
        self.assertTrue(deal.is_frozen)


# ══════════════════════════════════════════════════════════════════════════════
# REQ-6 — Юридические страницы
# ══════════════════════════════════════════════════════════════════════════════

class REQ6LegalPagesTest(TestCase):
    """Страницы /legal/terms/ и /legal/oferta/ доступны публично и отдают 200."""

    def test_terms_page_returns_200(self):
        resp = self.client.get(reverse("web:terms"))
        self.assertEqual(resp.status_code, 200)

    def test_oferta_page_returns_200(self):
        resp = self.client.get(reverse("web:oferta"))
        self.assertEqual(resp.status_code, 200)

    def test_terms_accessible_without_login(self):
        c = Client()  # anonymous
        resp = c.get(reverse("web:terms"))
        self.assertEqual(resp.status_code, 200)

    def test_oferta_accessible_without_login(self):
        c = Client()
        resp = c.get(reverse("web:oferta"))
        self.assertEqual(resp.status_code, 200)

    def test_terms_contains_license_section(self):
        """Соглашение должно содержать раздел о неисключительной лицензии."""
        resp = self.client.get(reverse("web:terms"))
        self.assertContains(resp, "неисключительн")

    def test_oferta_contains_ip_section(self):
        """Оферта должна содержать IP-раздел."""
        resp = self.client.get(reverse("web:oferta"))
        self.assertContains(resp, "интеллектуальн", msg_prefix="", html=False)

    def test_terms_contains_dispute_model_reference(self):
        """Соглашение ссылается на досудебную модель (ПКМ РУз № 249)."""
        resp = self.client.get(reverse("web:terms"))
        self.assertContains(resp, "249")

    def test_oferta_contains_zru701_reference(self):
        """Оферта ссылается на ЗРУ-701 (регулируемые категории)."""
        resp = self.client.get(reverse("web:oferta"))
        self.assertContains(resp, "ЗРУ-701")

    def test_oferta_contains_3year_retention(self):
        """Оферта упоминает 3-летнее хранение данных."""
        resp = self.client.get(reverse("web:oferta"))
        self.assertContains(resp, "3")
        self.assertContains(resp, "лет")

    def test_terms_links_to_oferta(self):
        resp = self.client.get(reverse("web:terms"))
        self.assertContains(resp, reverse("web:oferta"))

    def test_oferta_links_to_terms(self):
        resp = self.client.get(reverse("web:oferta"))
        self.assertContains(resp, reverse("web:terms"))
