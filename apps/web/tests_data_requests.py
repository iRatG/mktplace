"""«Мои данные»: обращения, сроки, очередь сотрудника, закрытие аккаунта с удалением данных."""
from datetime import timedelta
from decimal import Decimal

from django.core.files.base import ContentFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import PayoutRequisites, Transaction, Wallet, WithdrawalRequest
from apps.campaigns.models import Campaign
from apps.campaigns.models import Response as CampaignResponse
from apps.campaigns.testing import CARD_FIELDS, CLAIM_FIELDS, deal_from_response
from apps.deals import services as transitions
from apps.deals.models import Deal
from apps.notifications.models import Notification
from apps.platforms.models import Platform
from apps.registration.models import IdentityVerification, IPApplication
from apps.users import consents, data_requests
from apps.users.data_requests import DataRequestError
from apps.users.models import Consent, DataRequest, PersonalDataAccess, User


def _user(email="u@test.local", role=User.Role.BLOGGER, **extra):
    return User.objects.create_user(email=email, password="Test1234!", role=role, status=User.Status.ACTIVE,
                                    is_email_confirmed=True, **extra)


class RequestTermsTests(TestCase):
    def test_terms_by_kind(self):
        user = _user()
        now = timezone.now()
        info = data_requests.create_request(user, DataRequest.Kind.INFO)
        correct = data_requests.create_request(user, DataRequest.Kind.CORRECT)
        self.assertAlmostEqual(info.due_at, now + timedelta(days=10), delta=timedelta(minutes=1))
        self.assertAlmostEqual(correct.due_at, now + timedelta(days=3), delta=timedelta(minutes=1))

    def test_overdue(self):
        request = data_requests.create_request(_user(), DataRequest.Kind.INFO)
        self.assertFalse(request.is_overdue)
        request.due_at = timezone.now() - timedelta(minutes=1)
        self.assertTrue(request.is_overdue)
        request.status = DataRequest.Status.DONE
        self.assertFalse(request.is_overdue)

    def test_no_duplicate_open(self):
        user = _user()
        data_requests.create_request(user, DataRequest.Kind.INFO)
        with self.assertRaises(DataRequestError):
            data_requests.create_request(user, DataRequest.Kind.INFO)


class MyDataPageTests(TestCase):
    def setUp(self):
        self.user = _user()
        self.client.force_login(self.user)
        self.url = reverse("web:my_data")

    def test_page_shows_data_and_what_stays(self):
        consents.record(Consent.Kind.TERMS, user=self.user)
        response = self.client.get(self.url)
        self.assertContains(response, "data-held-data")
        self.assertContains(response, "data-closure-info")
        self.assertContains(response, "claim@ublogers.uz")
        self.assertContains(response, "data-my-consents")

    def test_create_notifies_staff(self):
        staff = _user("staff@test.local", is_staff=True)
        response = self.client.post(self.url, {"kind": "info", "text": "Какие данные?"})
        self.assertRedirects(response, self.url)
        request = DataRequest.objects.get()
        self.assertEqual((request.user, request.text), (self.user, "Какие данные?"))
        self.assertTrue(Notification.objects.filter(user=staff, url__contains=f"/data-requests/{request.pk}/").exists())

    def test_only_own_requests_shown(self):
        other = data_requests.create_request(_user("other@test.local"), DataRequest.Kind.INFO, "чужой текст")
        self.assertNotIn(other, self.client.get(self.url).context["requests"])

    def test_staff_redirected_and_anonymous_to_login(self):
        self.client.force_login(_user("staff@test.local", is_staff=True))
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.logout()
        self.assertIn("login", self.client.get(self.url)["Location"])

    def test_profile_links_to_my_data(self):
        self.assertContains(self.client.get(reverse("web:profile")), "data-my-data-link")


class StaffQueueTests(TestCase):
    def setUp(self):
        self.staff = _user("staff@test.local", is_staff=True)
        self.user = _user()
        self.item = data_requests.create_request(self.user, DataRequest.Kind.CORRECT, "Опечатка в имени")
        self.client.force_login(self.staff)

    def test_only_staff(self):
        self.client.force_login(self.user)
        self.assertNotEqual(self.client.get(reverse("web:admin_data_requests")).status_code, 200)
        self.assertNotEqual(self.client.get(reverse("web:admin_data_request_detail", args=[self.item.pk])).status_code, 200)

    def test_queue_marks_overdue_and_logs_access(self):
        DataRequest.objects.filter(pk=self.item.pk).update(due_at=timezone.now() - timedelta(days=1))
        response = self.client.get(reverse("web:admin_data_requests"))
        self.assertContains(response, "data-overdue")
        self.assertTrue(PersonalDataAccess.objects.filter(staff=self.staff).exists())

    def test_detail_logged_with_subject(self):
        self.client.get(reverse("web:admin_data_request_detail", args=[self.item.pk]))
        self.assertTrue(PersonalDataAccess.objects.filter(staff=self.staff, subject=self.user).exists())

    def test_dashboard_tile(self):
        self.assertEqual(self.client.get(reverse("web:admin_dashboard")).context["data_requests_open"], 1)

    def test_answer_required(self):
        url = reverse("web:admin_data_request_answer", args=[self.item.pk])
        self.client.post(url, {"action": "done", "answer": "  "})
        self.item.refresh_from_db()
        self.assertTrue(self.item.is_open)

    def test_answer_reaches_user(self):
        url = reverse("web:admin_data_request_answer", args=[self.item.pk])
        self.client.post(url, {"action": "done", "answer": "Исправили"})
        self.item.refresh_from_db()
        self.assertEqual((self.item.status, self.item.answer, self.item.answered_by),
                         (DataRequest.Status.DONE, "Исправили", self.staff))
        self.assertTrue(Notification.objects.filter(user=self.user, url=reverse("web:my_data")).exists())

    def test_answered_twice_rejected(self):
        data_requests.answer_request(self.item.pk, self.staff, DataRequest.Status.DONE, "Да")
        with self.assertRaises(DataRequestError):
            data_requests.answer_request(self.item.pk, self.staff, DataRequest.Status.REJECTED, "Нет")

    def test_closure_not_finished_by_plain_answer(self):
        request = data_requests.create_request(self.user, DataRequest.Kind.CLOSE)
        with self.assertRaises(DataRequestError):
            data_requests.answer_request(request.pk, self.staff, DataRequest.Status.DONE, "Готово")


class ClosureBlockersTests(TestCase):
    def setUp(self):
        self.user = _user()

    def test_clean_account_has_no_blockers(self):
        self.assertEqual(data_requests.closure_blockers(self.user), [])

    def test_money_blocks_including_reserve_and_payout(self):
        for field in ("available_balance", "reserved_balance", "on_withdrawal"):
            Wallet.objects.filter(user=self.user).update(available_balance=0, reserved_balance=0, on_withdrawal=0)
            Wallet.objects.filter(user=self.user).update(**{field: Decimal("1")})
            self.assertIn("на балансе есть деньги", data_requests.closure_blockers(self.user), field)

    def test_running_campaign_blocks(self):
        advertiser = _user("adv@test.local", User.Role.ADVERTISER)
        Campaign.objects.create(advertiser=advertiser, name="К", description="о", budget=Decimal("1000"),
                                status=Campaign.Status.ACTIVE)
        self.assertIn("есть незавершённые кампании", data_requests.closure_blockers(advertiser))

    def test_open_deal_and_claim_block(self):
        today = timezone.localdate()
        advertiser = _user("adv@test.local", User.Role.ADVERTISER)
        Wallet.objects.filter(user=advertiser).update(available_balance=Decimal("1000000"))
        campaign = Campaign.objects.create(
            advertiser=advertiser, name="К", payment_type=Campaign.PaymentType.FIXED, fixed_price=Decimal("100000"),
            budget=Decimal("1000000"), status=Campaign.Status.ACTIVE, start_date=today,
            end_date=today + timedelta(days=40), approval_required=False, **CARD_FIELDS,
        )
        platform = Platform.objects.create(blogger=self.user, social_type=Platform.SocialType.INSTAGRAM,
                                           url="https://instagram.com/block", subscribers=1, price_post=Decimal("1"),
                                           status=Platform.Status.APPROVED)
        response = CampaignResponse.objects.create(campaign=campaign, blogger=self.user, platform=platform,
                                                   content_type="post", proposed_price=Decimal("100000"))
        deal = deal_from_response(response, advertiser, publication_date=today)
        self.assertIn("есть незавершённые сделки", data_requests.closure_blockers(self.user))

        transitions.submit_publication(deal.pk, self.user, "https://instagram.com/p/1")
        transitions.open_claim(deal.pk, advertiser, **CLAIM_FIELDS)
        Deal.objects.filter(pk=deal.pk).update(status=Deal.Status.COMPLETED)
        self.assertEqual(data_requests.closure_blockers(self.user), ["есть нерассмотренные претензии"])

    def test_request_refused_with_reason(self):
        Wallet.objects.filter(user=self.user).update(available_balance=Decimal("5"))
        self.client.force_login(self.user)
        response = self.client.post(reverse("web:my_data"), {"kind": "close"})
        self.assertContains(response, "на балансе есть деньги")
        self.assertFalse(DataRequest.objects.exists())

    def test_money_arriving_after_request_stops_closure(self):
        staff = _user("staff@test.local", is_staff=True)
        request = data_requests.create_request(self.user, DataRequest.Kind.CLOSE)
        Wallet.objects.filter(user=self.user).update(available_balance=Decimal("5"))
        with self.assertRaises(DataRequestError):
            data_requests.close_account(request.pk, staff)
        self.user.refresh_from_db()
        self.assertEqual(self.user.status, User.Status.ACTIVE)


class CloseAccountTests(TestCase):
    def setUp(self):
        self.staff = _user("staff@test.local", is_staff=True)
        self.user = _user("blogger@test.local")
        profile = self.user.blogger_profile
        profile.nickname, profile.bio, profile.phone, profile.pinfl = "ник", "о себе", "+998901112233", "12345678901234"
        profile.show_in_catalog = True
        profile.save()
        IdentityVerification.objects.create(user=self.user, full_name="Иван", phone="+998901112233", pinfl="12345678901234")
        self.document = IPApplication.objects.create(user=self.user, document_type="self_employed_cert",
                                                     document_number="123")
        self.document.file.save("doc.pdf", ContentFile(b"data"))
        PayoutRequisites.objects.create(user=self.user, requisite_type="card", card_number="8600123412341234",
                                        card_holder_name="IVAN")
        self.platform = Platform.objects.create(blogger=self.user, social_type=Platform.SocialType.INSTAGRAM,
                                                url="https://instagram.com/closed", subscribers=1, avg_views=1,
                                                engagement_rate=1, status=Platform.Status.APPROVED)
        self.withdrawal_requisites = {"card_number": "8600123412341234"}
        self.withdrawal = WithdrawalRequest.objects.create(
            blogger=self.user, amount=Decimal("100"), status=WithdrawalRequest.Status.COMPLETED,
            requisites=self.withdrawal_requisites,
        )
        self.transaction = Transaction.objects.create(wallet=Wallet.objects.get(user=self.user),
                                                      type=Transaction.Type.EARNING, amount=Decimal("100"),
                                                      balance_after=Decimal("0"))
        self.consent = consents.record(Consent.Kind.TERMS, user=self.user)
        Notification.objects.create(user=self.user, type=Notification.Type.SYSTEM, title="т", body="т")
        self.request = data_requests.create_request(self.user, DataRequest.Kind.CLOSE)
        self.client.force_login(self.staff)
        self.url = reverse("web:admin_data_request_close_account", args=[self.request.pk])

    def _close(self):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(self.url, {"confirm": "on"})

    def test_needs_confirmation(self):
        self.client.post(self.url, {})
        self.request.refresh_from_db()
        self.assertTrue(self.request.is_open)

    def test_personal_data_removed(self):
        storage, name = self.document.file.storage, self.document.file.name
        self.assertTrue(storage.exists(name))
        self._close()
        self.user.refresh_from_db()
        profile = self.user.blogger_profile
        self.assertEqual((profile.nickname, profile.bio, profile.phone, profile.pinfl), ("", "", "", ""))
        self.assertFalse(profile.show_in_catalog)
        verification = IdentityVerification.objects.get(user=self.user)
        self.assertEqual((verification.full_name, verification.phone, verification.pinfl), ("", "", ""))
        self.document.refresh_from_db()
        self.assertEqual((self.document.file.name, self.document.document_number), ("", ""))
        self.assertFalse(storage.exists(name))
        self.assertFalse(PayoutRequisites.objects.filter(user=self.user).exists())
        self.assertFalse(Notification.objects.filter(user=self.user).exists())
        self.platform.refresh_from_db()
        self.assertEqual(self.platform.status, Platform.Status.REJECTED)

    def test_login_impossible(self):
        self._close()
        self.user.refresh_from_db()
        self.assertEqual((self.user.status, self.user.is_active), (User.Status.DELETED, False))
        self.assertNotIn("blogger@test.local", self.user.email)
        self.assertFalse(self.user.has_usable_password())
        self.client.logout()
        response = self.client.post(reverse("web:login"), {"email": "blogger@test.local", "password": "Test1234!"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_open_session_ends(self):
        user_client = self.client_class()
        user_client.force_login(self.user)
        self._close()
        self.assertIn("login", user_client.get(reverse("web:my_data"))["Location"])

    def test_records_with_other_basis_stay(self):
        self._close()
        self.withdrawal.refresh_from_db()
        self.assertEqual(self.withdrawal.requisites, self.withdrawal_requisites)
        self.assertTrue(Transaction.objects.filter(pk=self.transaction.pk).exists())
        self.assertTrue(Consent.objects.filter(pk=self.consent.pk).exists())
        self.request.refresh_from_db()
        self.assertEqual((self.request.status, self.request.answered_by), (DataRequest.Status.DONE, self.staff))
        self.assertTrue(PersonalDataAccess.objects.filter(subject=self.user, action__contains="Закрытие").exists())
        self.assertFalse(consents.is_granted(self.user, Consent.Kind.MARKETING))

    def test_same_email_can_register_again(self):
        self._close()
        self.assertTrue(_user("blogger@test.local").pk)

    def test_closed_twice_rejected(self):
        self._close()
        with self.assertRaises(DataRequestError):
            data_requests.close_account(self.request.pk, self.staff)
