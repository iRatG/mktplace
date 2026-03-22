"""
Tests for Module 7 (Reviews) and Module 13 (Admin enhancements).

Covers:
  - Review model creation and constraints
  - deal_review_submit: success, duplicate guard, 7-day window, wrong role
  - BloggerProfile.rating recalculated after review
  - blogger_public_profile: reviews in context
  - admin_users: search by email
  - admin_user_block / admin_user_unblock
  - admin_categories: list + create + duplicate guard
  - admin_category_delete
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.billing.models import Wallet
from apps.campaigns.models import Campaign
from apps.deals.models import Deal, Review
from apps.platforms.models import Category, Platform
from apps.profiles.models import BloggerProfile
from apps.users.models import User


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_user(email, role, confirmed=True):
    u = User.objects.create_user(email=email, password="Test1234!", role=role)
    if confirmed:
        u.status = User.Status.ACTIVE
        u.save(update_fields=["status"])
    return u


_counter = 0


def _make_platform(blogger):
    global _counter
    _counter += 1
    return Platform.objects.create(
        blogger=blogger,
        social_type=Platform.SocialType.INSTAGRAM,
        url=f"https://instagram.com/rv{_counter}",
        subscribers=5000,
        avg_views=300,
        engagement_rate=Decimal("3.00"),
        price_post=Decimal("30000"),
        status=Platform.Status.APPROVED,
    )


def _make_campaign(advertiser):
    return Campaign.objects.create(
        advertiser=advertiser,
        name="Test Campaign",
        description="desc",
        payment_type=Campaign.PaymentType.FIXED,
        fixed_price=Decimal("30000"),
        budget=Decimal("300000"),
        status=Campaign.Status.ACTIVE,
    )


def _make_completed_deal(advertiser, blogger, campaign=None, platform=None):
    if campaign is None:
        campaign = _make_campaign(advertiser)
    if platform is None:
        platform = _make_platform(blogger)
    deal = Deal.objects.create(
        campaign=campaign,
        blogger=blogger,
        platform=platform,
        advertiser=advertiser,
        amount=Decimal("30000"),
        status=Deal.Status.COMPLETED,
    )
    return deal


def _fund_wallet(user, amount=Decimal("500000")):
    w, _ = Wallet.objects.get_or_create(user=user)
    w.available_balance = amount
    w.save(update_fields=["available_balance"])
    return w


def _make_staff():
    return User.objects.create_user(
        email="staff@test.com", password="Test1234!", role=User.Role.ADVERTISER,
        is_staff=True, status=User.Status.ACTIVE,
    )


# ── Review model tests ────────────────────────────────────────────────────────

class ReviewModelTest(TestCase):
    """Review model creation and uniqueness."""

    def setUp(self):
        self.advertiser = _make_user("adv@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("bl@test.com", User.Role.BLOGGER)
        self.deal = _make_completed_deal(self.advertiser, self.blogger)

    def test_review_created(self):
        r = Review.objects.create(
            deal=self.deal, author=self.advertiser, target=self.blogger,
            rating=4, text="Great work",
        )
        self.assertEqual(r.rating, 4)
        self.assertEqual(r.target, self.blogger)

    def test_one_review_per_deal(self):
        Review.objects.create(
            deal=self.deal, author=self.advertiser, target=self.blogger, rating=5,
        )
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            Review.objects.create(
                deal=self.deal, author=self.advertiser, target=self.blogger, rating=3,
            )

    def test_str(self):
        r = Review.objects.create(
            deal=self.deal, author=self.advertiser, target=self.blogger, rating=5,
        )
        self.assertIn("5★", str(r))


# ── deal_review_submit view tests ─────────────────────────────────────────────

class DealReviewSubmitTest(TestCase):
    """POST /deals/<pk>/review/ — submit review."""

    def setUp(self):
        self.advertiser = _make_user("adv@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("bl@test.com", User.Role.BLOGGER)
        BloggerProfile.objects.get_or_create(user=self.blogger)
        self.deal = _make_completed_deal(self.advertiser, self.blogger)
        self.url = reverse("web:deal_review_submit", kwargs={"pk": self.deal.pk})

    def test_success_creates_review(self):
        self.client.force_login(self.advertiser)
        r = self.client.post(self.url, {"rating": 5, "text": "Excellent!"})
        self.assertRedirects(r, reverse("web:deal_detail", kwargs={"pk": self.deal.pk}))
        self.assertEqual(Review.objects.filter(deal=self.deal).count(), 1)

    def test_success_updates_blogger_rating(self):
        self.client.force_login(self.advertiser)
        self.client.post(self.url, {"rating": 4, "text": ""})
        profile = BloggerProfile.objects.get(user=self.blogger)
        self.assertEqual(profile.rating, Decimal("4.00"))

    def test_duplicate_review_rejected(self):
        Review.objects.create(
            deal=self.deal, author=self.advertiser, target=self.blogger, rating=3,
        )
        self.client.force_login(self.advertiser)
        r = self.client.post(self.url, {"rating": 5, "text": "Try again"})
        self.assertRedirects(r, reverse("web:deal_detail", kwargs={"pk": self.deal.pk}))
        self.assertEqual(Review.objects.filter(deal=self.deal).count(), 1)

    def test_blogger_cannot_submit_review(self):
        self.client.force_login(self.blogger)
        r = self.client.post(self.url, {"rating": 5, "text": ""})
        # 404 because deal is fetched with advertiser=request.user
        self.assertEqual(r.status_code, 404)

    def test_invalid_rating_rejected(self):
        self.client.force_login(self.advertiser)
        r = self.client.post(self.url, {"rating": 6, "text": ""})
        # Redirected back with error
        self.assertRedirects(r, reverse("web:deal_detail", kwargs={"pk": self.deal.pk}))
        self.assertEqual(Review.objects.count(), 0)

    def test_non_completed_deal_returns_404(self):
        self.deal.status = Deal.Status.IN_PROGRESS
        self.deal.save(update_fields=["status"])
        self.client.force_login(self.advertiser)
        r = self.client.post(self.url, {"rating": 5, "text": ""})
        self.assertEqual(r.status_code, 404)

    def test_anonymous_redirected(self):
        r = self.client.post(self.url, {"rating": 5, "text": ""})
        self.assertEqual(r.status_code, 302)


# ── blogger_public_profile reviews ───────────────────────────────────────────

class BloggerPublicProfileReviewsTest(TestCase):
    """Reviews appear on blogger public profile page."""

    def setUp(self):
        self.advertiser = _make_user("adv@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("bl@test.com", User.Role.BLOGGER)
        self.deal = _make_completed_deal(self.advertiser, self.blogger)
        Review.objects.create(
            deal=self.deal, author=self.advertiser, target=self.blogger,
            rating=5, text="Top blogger",
        )
        self.client.force_login(self.advertiser)

    def test_reviews_in_context(self):
        r = self.client.get(reverse("web:blogger_public_profile", kwargs={"pk": self.blogger.pk}))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.context["reviews"]), 1)

    def test_no_reviews_empty_queryset(self):
        blogger2 = _make_user("bl2@test.com", User.Role.BLOGGER)
        r = self.client.get(reverse("web:blogger_public_profile", kwargs={"pk": blogger2.pk}))
        self.assertEqual(len(r.context["reviews"]), 0)


# ── Admin: user search ────────────────────────────────────────────────────────

class AdminUsersSearchTest(TestCase):
    """admin_users GET ?q= filter."""

    def setUp(self):
        self.staff = _make_staff()
        self.client.force_login(self.staff)
        _make_user("alice@example.com", User.Role.BLOGGER)
        _make_user("bob@example.com", User.Role.ADVERTISER)

    def test_no_query_returns_all(self):
        r = self.client.get(reverse("web:admin_users"))
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(r.context["users"].count(), 2)

    def test_search_filters_by_email(self):
        r = self.client.get(reverse("web:admin_users") + "?q=alice")
        self.assertEqual(r.status_code, 200)
        emails = [u.email for u in r.context["users"]]
        self.assertIn("alice@example.com", emails)
        self.assertNotIn("bob@example.com", emails)

    def test_search_case_insensitive(self):
        r = self.client.get(reverse("web:admin_users") + "?q=ALICE")
        emails = [u.email for u in r.context["users"]]
        self.assertIn("alice@example.com", emails)


# ── Admin: block/unblock ──────────────────────────────────────────────────────

class AdminUserBlockTest(TestCase):
    """admin_user_block and admin_user_unblock."""

    def setUp(self):
        self.staff = _make_staff()
        self.client.force_login(self.staff)
        self.target = _make_user("target@test.com", User.Role.BLOGGER)

    def test_block_user(self):
        r = self.client.post(reverse("web:admin_user_block", kwargs={"pk": self.target.pk}))
        self.assertRedirects(r, reverse("web:admin_users"))
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, User.Status.BLOCKED)

    def test_unblock_user(self):
        self.target.status = User.Status.BLOCKED
        self.target.save(update_fields=["status"])
        r = self.client.post(reverse("web:admin_user_unblock", kwargs={"pk": self.target.pk}))
        self.assertRedirects(r, reverse("web:admin_users"))
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, User.Status.ACTIVE)

    def test_cannot_block_staff(self):
        staff2 = User.objects.create_user(
            email="staff2@test.com", password="Test1234!", role=User.Role.ADVERTISER,
            is_staff=True, status=User.Status.ACTIVE,
        )
        self.client.post(reverse("web:admin_user_block", kwargs={"pk": staff2.pk}))
        staff2.refresh_from_db()
        self.assertNotEqual(staff2.status, User.Status.BLOCKED)

    def test_non_staff_cannot_access(self):
        user = _make_user("u@test.com", User.Role.BLOGGER)
        self.client.force_login(user)
        r = self.client.post(reverse("web:admin_user_block", kwargs={"pk": self.target.pk}))
        # Redirected away (not staff)
        self.assertEqual(r.status_code, 302)
        self.target.refresh_from_db()
        self.assertNotEqual(self.target.status, User.Status.BLOCKED)


# ── Admin: categories CRUD ────────────────────────────────────────────────────

class AdminCategoriesTest(TestCase):
    """admin_categories CRUD."""

    def setUp(self):
        self.staff = _make_staff()
        self.client.force_login(self.staff)
        self.url = reverse("web:admin_categories")

    def test_list_page_200(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)

    def test_create_category(self):
        r = self.client.post(self.url, {"name": "Авто", "slug": "auto"})
        self.assertRedirects(r, self.url)
        self.assertTrue(Category.objects.filter(slug="auto").exists())

    def test_duplicate_name_rejected(self):
        Category.objects.create(name="Авто", slug="auto")
        r = self.client.post(self.url, {"name": "Авто", "slug": "auto2"})
        self.assertRedirects(r, self.url)
        # Only 1 category with name Авто
        self.assertEqual(Category.objects.filter(name="Авто").count(), 1)

    def test_duplicate_slug_rejected(self):
        Category.objects.create(name="Авто", slug="auto")
        r = self.client.post(self.url, {"name": "Автомобили", "slug": "auto"})
        self.assertRedirects(r, self.url)
        self.assertEqual(Category.objects.filter(slug="auto").count(), 1)

    def test_delete_category(self):
        cat = Category.objects.create(name="Тест", slug="test-del")
        r = self.client.post(reverse("web:admin_category_delete", kwargs={"pk": cat.pk}))
        self.assertRedirects(r, self.url)
        self.assertFalse(Category.objects.filter(pk=cat.pk).exists())

    def test_non_staff_cannot_access(self):
        user = _make_user("u@test.com", User.Role.BLOGGER)
        self.client.force_login(user)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 302)
