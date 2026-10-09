"""
#33 часть 2: напоминание исполнителю накануне даты публикации, просрочка без автоотмены, перенос даты по согласию
сторон (сайт и API одинаково).
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.billing.models import Wallet
from apps.campaigns.models import Campaign
from apps.campaigns.models import Response as CampaignResponse
from apps.campaigns.testing import deal_from_response
from apps.campaigns.validation import publication_calendar, publication_date_error
from apps.deals import services as transitions
from apps.deals.models import ChatMessage, Deal, DealStatusLog, PublicationDateChange
from apps.deals.services import TransitionError
from apps.notifications.models import Notification
from apps.platforms.models import Platform
from apps.users.models import User

S = Deal.Status


class PublicationScheduleTest(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.adv = User.objects.create_user(email="adv@test.com", password="pass1234", role=User.Role.ADVERTISER,
                                            status=User.Status.ACTIVE)
        Wallet.objects.filter(user=self.adv).update(available_balance=Decimal("1000000"))
        self.blogger = User.objects.create_user(email="bl@test.com", password="pass1234", role=User.Role.BLOGGER,
                                                status=User.Status.ACTIVE)
        self.other = User.objects.create_user(email="other@test.com", password="pass1234", role=User.Role.BLOGGER,
                                              status=User.Status.ACTIVE)
        platform = Platform.objects.create(
            blogger=self.blogger, social_type=Platform.SocialType.INSTAGRAM, url="https://instagram.com/sched",
            subscribers=12000, price_post=Decimal("50000"), status=Platform.Status.APPROVED,
        )
        self.campaign = Campaign.objects.create(
            advertiser=self.adv, name="Кампания", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("150000"), budget=Decimal("1500000"), status=Campaign.Status.ACTIVE,
            start_date=self.today, end_date=self.today + timedelta(days=30),
        )
        resp = CampaignResponse.objects.create(
            campaign=self.campaign, blogger=self.blogger, platform=platform, content_type="post",
            proposed_price=Decimal("150000"),
        )
        self.deal = deal_from_response(resp, self.adv, publication_date=self.today + timedelta(days=1))
        Notification.objects.all().delete()

    def _set(self, **fields):
        Deal.objects.filter(pk=self.deal.pk).update(**fields)
        self.deal.refresh_from_db()

    def _notes(self, user):
        return Notification.objects.filter(user=user, related_deal=self.deal)

    # ── Напоминание накануне ─────────────────────────────────────────────────

    def test_reminder_day_before_once(self):
        self.assertEqual(transitions.send_publication_reminders(), 1)
        self.assertEqual(transitions.send_publication_reminders(), 0)
        notes = self._notes(self.blogger)
        self.assertEqual(notes.count(), 1)
        self.assertIn("Завтра", notes.get().title)
        self.assertFalse(self._notes(self.adv).exists())

    def test_no_reminder_after_publication_or_for_later_date(self):
        self._set(status=S.CHECKING)
        self.assertEqual(transitions.send_publication_reminders(), 0)
        self._set(status=S.IN_PROGRESS, publication_date=self.today + timedelta(days=2))
        self.assertEqual(transitions.send_publication_reminders(), 0)

    def test_reminder_while_on_approval(self):
        self._set(status=S.ON_APPROVAL)
        self.assertEqual(transitions.send_publication_reminders(), 1)

    # ── Просрочка ────────────────────────────────────────────────────────────

    def test_overdue_notifies_both_once_and_keeps_status_and_money(self):
        self._set(status=S.WAITING_PUBLICATION, publication_date=self.today - timedelta(days=2))
        reserved = Wallet.objects.get(user=self.adv).reserved_balance
        self.assertEqual(self.deal.overdue_days, 2)
        self.assertEqual(transitions.notify_overdue_publications(), 1)
        self.assertEqual(transitions.notify_overdue_publications(), 0)
        self.assertEqual(self._notes(self.blogger).count(), 1)
        self.assertEqual(self._notes(self.adv).count(), 1)
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, S.WAITING_PUBLICATION)
        self.assertEqual(Wallet.objects.get(user=self.adv).reserved_balance, reserved)

        self.client.force_login(self.adv)
        page = self.client.get(reverse("web:deal_detail", kwargs={"pk": self.deal.pk})).content.decode()
        self.assertIn("Просрочено на 2 дн.", page)

    def test_on_approval_is_not_overdue(self):
        self._set(status=S.ON_APPROVAL, publication_date=self.today - timedelta(days=1))
        self.assertEqual(self.deal.overdue_days, 0)
        self.assertEqual(transitions.notify_overdue_publications(), 0)
        self.client.force_login(self.blogger)
        page = self.client.get(reverse("web:deal_detail", kwargs={"pk": self.deal.pk})).content.decode()
        self.assertNotIn("data-publication-overdue", page)

    def test_today_is_not_overdue(self):
        self._set(publication_date=self.today)
        self.assertEqual(self.deal.overdue_days, 0)
        self.assertEqual(transitions.notify_overdue_publications(), 0)

    # ── Перенос по согласию ──────────────────────────────────────────────────

    def test_reschedule_accepted(self):
        old = self.deal.publication_date
        new = self.today + timedelta(days=10)
        self._set(publication_reminder_sent_at=timezone.now(), overdue_notified_at=timezone.now())
        change = transitions.propose_publication_date(self.deal.pk, self.blogger, new)
        self.assertEqual(change.old_date, old)
        self.assertEqual(self._notes(self.adv).count(), 1)
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.publication_date, old)  # до согласия дата прежняя

        transitions.accept_publication_date(self.deal.pk, self.adv)
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.publication_date, new)
        self.assertIsNone(self.deal.publication_reminder_sent_at)
        self.assertIsNone(self.deal.overdue_notified_at)
        self.assertEqual(self.deal.status, S.IN_PROGRESS)
        change.refresh_from_db()
        self.assertEqual(change.status, PublicationDateChange.Status.ACCEPTED)
        self.assertTrue(self._notes(self.blogger).filter(title="Перенос даты принят").exists())
        text = f"Дата публикации перенесена с {old:%d.%m.%Y} на {new:%d.%m.%Y}"
        self.assertTrue(DealStatusLog.objects.filter(deal=self.deal, comment__startswith=text).exists())
        self.assertTrue(ChatMessage.objects.filter(deal=self.deal, is_system=True, text__startswith=text).exists())
        self.assertIn(new, dict(publication_calendar(self.campaign)))
        self.assertNotIn(old, dict(publication_calendar(self.campaign)))

    def test_reschedule_declined_then_new_proposal(self):
        old = self.deal.publication_date
        transitions.propose_publication_date(self.deal.pk, self.adv, self.today + timedelta(days=3))
        transitions.decline_publication_date(self.deal.pk, self.blogger)
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.publication_date, old)
        self.assertTrue(self._notes(self.adv).filter(title="Перенос даты отклонён").exists())
        transitions.propose_publication_date(self.deal.pk, self.blogger, self.today + timedelta(days=4))

    def test_reschedule_rules(self):
        with self.assertRaisesMessage(TransitionError, "участник"):
            transitions.propose_publication_date(self.deal.pk, self.other, self.today + timedelta(days=3))
        with self.assertRaisesMessage(TransitionError, "совпадает"):
            transitions.propose_publication_date(self.deal.pk, self.blogger, self.deal.publication_date)
        late = self.campaign.end_date + timedelta(days=1)
        with self.assertRaisesMessage(TransitionError, publication_date_error(self.campaign, late)):
            transitions.propose_publication_date(self.deal.pk, self.blogger, late)
        with self.assertRaisesMessage(TransitionError, "в прошлом"):
            transitions.propose_publication_date(self.deal.pk, self.blogger, self.today - timedelta(days=1))

        transitions.propose_publication_date(self.deal.pk, self.blogger, self.today + timedelta(days=3))
        with self.assertRaisesMessage(TransitionError, "ещё ждёт ответа"):
            transitions.propose_publication_date(self.deal.pk, self.adv, self.today + timedelta(days=4))
        with self.assertRaisesMessage(TransitionError, "вторая сторона"):
            transitions.accept_publication_date(self.deal.pk, self.blogger)
        with self.assertRaisesMessage(TransitionError, "участник"):
            transitions.decline_publication_date(self.deal.pk, self.other)

    def test_accept_rechecks_date(self):
        change = transitions.propose_publication_date(self.deal.pk, self.blogger, self.today + timedelta(days=3))
        PublicationDateChange.objects.filter(pk=change.pk).update(new_date=self.today - timedelta(days=1))
        with self.assertRaisesMessage(TransitionError, "Предложите другую дату"):
            transitions.accept_publication_date(self.deal.pk, self.adv)

    def test_publication_closes_pending_change(self):
        self._set(publication_date=self.today)
        transitions.propose_publication_date(self.deal.pk, self.adv, self.today + timedelta(days=3))
        transitions.submit_publication(self.deal.pk, self.blogger, "https://instagram.com/p/1")
        self.assertEqual(PublicationDateChange.objects.get().status, PublicationDateChange.Status.CLOSED)
        with self.assertRaises(TransitionError):
            transitions.accept_publication_date(self.deal.pk, self.blogger)
        with self.assertRaisesMessage(TransitionError, "пока публикации нет"):
            transitions.propose_publication_date(self.deal.pk, self.blogger, self.today + timedelta(days=4))

    def test_no_date_no_reschedule(self):
        self._set(publication_date=None)
        with self.assertRaisesMessage(TransitionError, "нет даты"):
            transitions.propose_publication_date(self.deal.pk, self.blogger, self.today + timedelta(days=3))

    # ── Сайт ─────────────────────────────────────────────────────────────────

    def test_web_propose_and_accept(self):
        new = self.today + timedelta(days=7)
        url = reverse("web:deal_detail", kwargs={"pk": self.deal.pk})
        self.client.force_login(self.blogger)
        self.assertIn("data-reschedule", self.client.get(url).content.decode())
        self.client.post(reverse("web:deal_propose_publication_date", kwargs={"pk": self.deal.pk}),
                         {"publication_date": new.isoformat()})
        page = self.client.get(url).content.decode()
        self.assertIn("Вы предложили", page)
        self.assertNotIn(reverse("web:deal_accept_publication_date", kwargs={"pk": self.deal.pk}), page)

        self.client.force_login(self.adv)
        page = self.client.get(url).content.decode()
        self.assertIn(reverse("web:deal_accept_publication_date", kwargs={"pk": self.deal.pk}), page)
        self.client.post(reverse("web:deal_accept_publication_date", kwargs={"pk": self.deal.pk}))
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.publication_date, new)

    def test_web_bad_date_and_outsider(self):
        self.client.force_login(self.blogger)
        self.client.post(reverse("web:deal_propose_publication_date", kwargs={"pk": self.deal.pk}),
                         {"publication_date": "abc"})
        self.assertFalse(PublicationDateChange.objects.exists())
        self.client.force_login(self.other)
        r = self.client.post(reverse("web:deal_propose_publication_date", kwargs={"pk": self.deal.pk}),
                             {"publication_date": (self.today + timedelta(days=3)).isoformat()})
        self.assertEqual(r.status_code, 404)

    def test_web_decline(self):
        transitions.propose_publication_date(self.deal.pk, self.blogger, self.today + timedelta(days=3))
        self.client.force_login(self.adv)
        self.client.post(reverse("web:deal_decline_publication_date", kwargs={"pk": self.deal.pk}))
        self.assertEqual(PublicationDateChange.objects.get().status, PublicationDateChange.Status.DECLINED)

    # ── API ──────────────────────────────────────────────────────────────────

    def test_api_propose_accept_decline(self):
        api = APIClient()
        base = f"/api/v1/deals/{self.deal.pk}/"
        api.force_authenticate(self.adv)
        self.assertEqual(api.post(base + "propose-publication-date/", {"publication_date": "x"}).status_code, 400)
        new = self.today + timedelta(days=6)
        r = api.post(base + "propose-publication-date/", {"publication_date": new.isoformat()})
        self.assertEqual(r.status_code, 200, r.content)
        data = api.get(base).json()
        self.assertEqual(data["pending_date_change"]["new_date"], new.isoformat())
        self.assertEqual(data["overdue_days"], 0)
        self.assertEqual(api.post(base + "accept-publication-date/").status_code, 400)  # своё не принять

        api.force_authenticate(self.blogger)
        self.assertEqual(api.post(base + "decline-publication-date/").status_code, 200)
        self.assertTrue(self._notes(self.adv).filter(title="Перенос даты отклонён").exists())
        api.post(base + "propose-publication-date/", {"publication_date": new.isoformat()})
        api.force_authenticate(self.adv)
        self.assertEqual(api.post(base + "accept-publication-date/").status_code, 200)
        data = api.get(base).json()
        self.assertEqual(data["publication_date"], new.isoformat())
        self.assertIsNone(data["pending_date_change"])
