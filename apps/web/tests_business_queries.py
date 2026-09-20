"""
Tests for business_queries module (internal IT tickets + password-gated business surveys).

Covers:
  - ticket_create: logs initial NEW status via TicketStatusLog
  - _it_team_required: staff without "IT Team" group is denied
  - _it_team_required: staff in "IT Team" group is allowed
  - business_query_view: wrong password rejected, doesn't unlock
  - business_query_view: correct password unlocks, submission saved and shown in history
  - business_query_view: rate limit — 9th consecutive wrong password attempt -> 429
"""

from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.business_queries.models import BusinessQuery, BusinessQuestion, TicketStatusLog
from apps.users.models import User

_counter = 0


def _make_staff(email, in_it_team=False):
    global _counter
    _counter += 1
    user = User.objects.create_user(email=email, password="Test1234!", is_staff=True)
    if in_it_team:
        group, _ = Group.objects.get_or_create(name="IT Team")
        user.groups.add(group)
    return user


def _make_query(password="secret123"):
    query = BusinessQuery.objects.create(title="Test survey")
    query.set_password(password)
    query.save(update_fields=["password_hash"])
    BusinessQuestion.objects.create(query=query, order=0, kind=BusinessQuestion.Kind.CHOICE,
                                     title="Q1", option_a="A text", option_b="B text")
    BusinessQuestion.objects.create(query=query, order=1, kind=BusinessQuestion.Kind.NOTE, title="Q2")
    return query


class TicketTests(TestCase):
    def test_ticket_create_logs_new_status(self):
        staff = _make_staff("it1@demo.com", in_it_team=True)
        self.client.force_login(staff)
        response = self.client.post(reverse("web:ticket_create"), {
            "title": "Заголовок тикета",
            "description": "Описание проблемы",
        })
        self.assertEqual(response.status_code, 302)
        log = TicketStatusLog.objects.latest("created_at")
        self.assertEqual(log.new_status, "new")
        self.assertEqual(log.ticket.title, "Заголовок тикета")


class ItTeamAccessTests(TestCase):
    def test_staff_without_group_denied(self):
        staff = _make_staff("staff_no_group@demo.com", in_it_team=False)
        self.client.force_login(staff)
        response = self.client.get(reverse("web:ticket_list"))
        self.assertEqual(response.status_code, 302)

    def test_staff_with_group_allowed(self):
        staff = _make_staff("staff_it_team@demo.com", in_it_team=True)
        self.client.force_login(staff)
        response = self.client.get(reverse("web:ticket_list"))
        self.assertEqual(response.status_code, 200)


class ItTeamGroupCheckTests(TestCase):
    """Тот же класс риска, что и с группой "Регистрация юрлиц" (см.
    apps.web.tests_registration.ReviewerPoolCheckTests): если "IT Team"
    опустеет, /tickets/ и admin для Ticket станут недоступны всем молча."""

    def test_warns_when_group_empty(self):
        from apps.business_queries.checks import check_it_team_group

        errors = check_it_team_group(None)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "apps.business_queries.W001")

    def test_no_warning_when_group_has_active_member(self):
        from apps.business_queries.checks import check_it_team_group

        _make_staff("it_member_for_check@demo.com", in_it_team=True)
        self.assertEqual(check_it_team_group(None), [])


class BusinessQueryViewTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_wrong_password_does_not_unlock(self):
        query = _make_query()
        url = reverse("web:business_query", args=[query.token])
        response = self.client.post(url, {"action": "unlock", "password": "nope"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Неверный пароль")

    def test_correct_password_unlocks_and_submission_recorded(self):
        query = _make_query()
        url = reverse("web:business_query", args=[query.token])
        self.client.post(url, {"action": "unlock", "password": "secret123"})

        questions = list(query.questions.order_by("order"))
        data = {"action": "submit", "responder_name": "Тест Тестов", "comment": "ок"}
        for q in questions:
            data[f"q_{q.pk}"] = "A" if q.kind == BusinessQuestion.Kind.CHOICE else "свободный ответ"

        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Тест Тестов")
        self.assertEqual(query.submissions.count(), 1)

    def test_rate_limit_after_nine_wrong_attempts(self):
        query = _make_query()
        url = reverse("web:business_query", args=[query.token])
        for _ in range(8):
            response = self.client.post(url, {"action": "unlock", "password": "nope"})
            self.assertEqual(response.status_code, 200)
        response = self.client.post(url, {"action": "unlock", "password": "nope"})
        self.assertEqual(response.status_code, 429)
