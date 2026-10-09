"""
#46: отзывы о платформе — пользователь оставляет отзыв (можно анонимно), отрицательный — с причиной, без повторов;
публикуется после проверки сотрудником; сотрудник может отказать с причиной или удалить опубликованный.
"""
from django.test import TestCase
from django.urls import reverse

from apps.notifications.models import Notification
from apps.users.models import User

from .models import PlatformReview
from .services import ReviewError, moderate_review, remove_review, submit_review

TEXT = "Нашёл блогеров за день, сделка прошла гладко, деньги пришли вовремя."


class PlatformReviewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="u@test.com", password="pass1234", role=User.Role.ADVERTISER,
                                             status=User.Status.ACTIVE)
        self.staff = User.objects.create_user(email="s@test.com", password="pass1234", is_staff=True,
                                              status=User.Status.ACTIVE)

    def test_rules(self):
        with self.assertRaisesMessage(ReviewError, "оценку"):
            submit_review(self.user, 7, TEXT)
        with self.assertRaisesMessage(ReviewError, "пару предложений"):
            submit_review(self.user, 5, "Супер")
        with self.assertRaisesMessage(ReviewError, "чем вы остались недовольны"):
            submit_review(self.user, 2, "Долго ждал ответа поддержки, неудобно искать кампании.")
        submit_review(self.user, 5, TEXT)
        with self.assertRaisesMessage(ReviewError, "уже оставляли"):
            submit_review(self.user, 4, TEXT.upper())

    def test_moderation_flow(self):
        review = submit_review(self.user, 5, TEXT, is_anonymous=True)
        self.assertEqual(review.status, PlatformReview.Status.PENDING)
        self.assertTrue(Notification.objects.filter(user=self.staff, title="Новый отзыв о платформе").exists())
        page = self.client.get(reverse("web:platform_reviews")).content.decode()
        self.assertNotIn(TEXT, page)  # до проверки не виден

        with self.assertRaisesMessage(ReviewError, "сотрудник"):
            moderate_review(review.pk, self.user, True)
        moderate_review(review.pk, self.staff, True)
        page = self.client.get(reverse("web:platform_reviews")).content.decode()
        self.assertIn(TEXT, page)
        self.assertIn("Аноним", page)
        self.assertTrue(Notification.objects.filter(user=self.user, title="Отзыв опубликован").exists())

        with self.assertRaisesMessage(ReviewError, "причину"):
            remove_review(review.pk, self.staff, "")
        remove_review(review.pk, self.staff, "Нарушает права третьих лиц")
        self.assertNotIn(TEXT, self.client.get(reverse("web:platform_reviews")).content.decode())

    def test_reject_needs_reason(self):
        review = submit_review(self.user, 1, "Не смог вывести деньги, поддержка не ответила.", reason="Вывод")
        with self.assertRaisesMessage(ReviewError, "требование"):
            moderate_review(review.pk, self.staff, False)
        moderate_review(review.pk, self.staff, False, "Есть ненормативная лексика")
        self.assertTrue(Notification.objects.filter(user=self.user, title="Отзыв не опубликован").exists())

    def test_web_submit_and_panel(self):
        self.client.force_login(self.user)
        self.client.post(reverse("web:platform_reviews"), {"rating": "5", "text": TEXT})
        review = PlatformReview.objects.get()
        self.client.force_login(self.staff)
        self.assertIn("data-reviews-tile", self.client.get(reverse("web:admin_dashboard")).content.decode())
        panel = self.client.get(reverse("web:admin_platform_reviews")).content.decode()
        self.assertIn("data-pending-review", panel)
        self.client.post(reverse("web:admin_platform_review_moderate", kwargs={"pk": review.pk}), {"decision": "publish"})
        review.refresh_from_db()
        self.assertEqual(review.status, PlatformReview.Status.PUBLISHED)

    def test_anonymous_visitor_sees_list_but_cannot_post(self):
        r = self.client.get(reverse("web:platform_reviews"))
        self.assertEqual(r.status_code, 200)
        r = self.client.post(reverse("web:platform_reviews"), {"rating": "5", "text": TEXT})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(PlatformReview.objects.exists())
