"""
Tests for Module 10 — Blogger Catalog & Direct Offers.
Covers: catalog access, filtering, direct_offer_create, accept, reject.
"""

from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from apps.billing.models import Wallet
from apps.campaigns.models import Campaign, DirectOffer
from apps.deals.models import Deal
from apps.platforms.models import Category, Platform
from apps.profiles.models import BloggerProfile
from apps.users.models import User


def _make_user(email, role, confirmed=True):
    u = User.objects.create_user(email=email, password="Test1234!", role=role)
    if confirmed:
        u.status = User.Status.ACTIVE
        u.save(update_fields=["status"])
    return u


_platform_counter = 0


def _make_platform(blogger, status=Platform.Status.APPROVED, social_type=None):
    global _platform_counter
    _platform_counter += 1
    return Platform.objects.create(
        blogger=blogger,
        social_type=social_type or Platform.SocialType.INSTAGRAM,
        url=f"https://instagram.com/p{_platform_counter}_{blogger.pk}",
        subscribers=10000,
        avg_views=500,
        engagement_rate=Decimal("3.50"),
        price_post=Decimal("50000"),
        status=status,
    )


def _make_campaign(advertiser, status=Campaign.Status.ACTIVE):
    return Campaign.objects.create(
        advertiser=advertiser,
        name="Test Campaign",
        description="desc",
        payment_type=Campaign.PaymentType.FIXED,
        fixed_price=Decimal("50000"),
        budget=Decimal("500000"),
        status=status,
    )


class CatalogAccessTest(TestCase):
    """Who can and cannot access /bloggers/."""

    def setUp(self):
        self.advertiser = _make_user("adv@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("blogger@test.com", User.Role.BLOGGER)
        self.staff = _make_user("staff@test.com", User.Role.ADVERTISER)
        self.staff.is_staff = True
        self.staff.save(update_fields=["is_staff"])
        self.url = reverse("web:blogger_catalog")

    def test_advertiser_can_access(self):
        self.client.force_login(self.advertiser)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)

    def test_staff_can_access(self):
        self.client.force_login(self.staff)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)

    def test_blogger_redirected(self):
        self.client.force_login(self.blogger)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 302)

    def test_anonymous_redirected_to_login(self):
        r = self.client.get(self.url)
        self.assertRedirects(r, f"{reverse('web:login')}?next={self.url}")


class CatalogContentTest(TestCase):
    """Catalog shows only APPROVED platforms."""

    def setUp(self):
        self.advertiser = _make_user("adv@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("blogger@test.com", User.Role.BLOGGER)
        self.client.force_login(self.advertiser)

    def test_only_approved_platforms_shown(self):
        approved = _make_platform(self.blogger, Platform.Status.APPROVED)
        pending = _make_platform(self.blogger, Platform.Status.PENDING)
        pending.url = "https://instagram.com/pending"
        pending.save()

        r = self.client.get(reverse("web:blogger_catalog"))
        platforms = list(r.context["platforms"])
        pks = [p.pk for p in platforms]
        self.assertIn(approved.pk, pks)
        self.assertNotIn(pending.pk, pks)

    def test_total_count_in_context(self):
        _make_platform(self.blogger)
        r = self.client.get(reverse("web:blogger_catalog"))
        self.assertIn("total", r.context)
        self.assertEqual(r.context["total"], 1)


class CatalogFilterTest(TestCase):
    """Catalog filtering by social_type, min_subscribers, min_er."""

    def setUp(self):
        self.advertiser = _make_user("adv@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("b1@test.com", User.Role.BLOGGER)
        self.blogger2 = _make_user("b2@test.com", User.Role.BLOGGER)
        self.client.force_login(self.advertiser)

        self.p_insta = Platform.objects.create(
            blogger=self.blogger,
            social_type=Platform.SocialType.INSTAGRAM,
            url="https://instagram.com/b1",
            subscribers=50000,
            engagement_rate=Decimal("5.00"),
            status=Platform.Status.APPROVED,
        )
        self.p_telegram = Platform.objects.create(
            blogger=self.blogger2,
            social_type=Platform.SocialType.TELEGRAM,
            url="https://t.me/b2",
            subscribers=5000,
            engagement_rate=Decimal("1.50"),
            status=Platform.Status.APPROVED,
        )

    def test_filter_by_social_type(self):
        r = self.client.get(reverse("web:blogger_catalog"), {"social_type": "instagram"})
        pks = [p.pk for p in r.context["platforms"]]
        self.assertIn(self.p_insta.pk, pks)
        self.assertNotIn(self.p_telegram.pk, pks)

    def test_filter_by_min_subscribers(self):
        r = self.client.get(reverse("web:blogger_catalog"), {"min_subscribers": "10000"})
        pks = [p.pk for p in r.context["platforms"]]
        self.assertIn(self.p_insta.pk, pks)
        self.assertNotIn(self.p_telegram.pk, pks)

    def test_filter_by_min_er(self):
        r = self.client.get(reverse("web:blogger_catalog"), {"min_er": "3"})
        pks = [p.pk for p in r.context["platforms"]]
        self.assertIn(self.p_insta.pk, pks)
        self.assertNotIn(self.p_telegram.pk, pks)

    def test_filter_by_category(self):
        cat = Category.objects.create(name="Tech", slug="tech")
        self.p_insta.categories.add(cat)
        r = self.client.get(reverse("web:blogger_catalog"), {"category": cat.pk})
        pks = [p.pk for p in r.context["platforms"]]
        self.assertIn(self.p_insta.pk, pks)
        self.assertNotIn(self.p_telegram.pk, pks)

    def test_filter_by_max_subscribers(self):
        r = self.client.get(reverse("web:blogger_catalog"), {"max_subscribers": "10000"})
        pks = [p.pk for p in r.context["platforms"]]
        self.assertIn(self.p_telegram.pk, pks)
        self.assertNotIn(self.p_insta.pk, pks)

    def test_filter_by_min_price(self):
        self.p_insta.price_post = Decimal("10000")
        self.p_insta.save(update_fields=["price_post"])
        self.p_telegram.price_post = Decimal("500")
        self.p_telegram.save(update_fields=["price_post"])
        r = self.client.get(reverse("web:blogger_catalog"), {"min_price": "5000"})
        pks = [p.pk for p in r.context["platforms"]]
        self.assertIn(self.p_insta.pk, pks)
        self.assertNotIn(self.p_telegram.pk, pks)

    def test_filter_by_max_er(self):
        r = self.client.get(reverse("web:blogger_catalog"), {"max_er": "3"})
        pks = [p.pk for p in r.context["platforms"]]
        self.assertIn(self.p_telegram.pk, pks)
        self.assertNotIn(self.p_insta.pk, pks)

    def test_filter_by_min_rating(self):
        from apps.profiles.models import BloggerProfile
        profile1 = BloggerProfile.objects.get(user=self.blogger)
        profile1.rating = Decimal("4.50")
        profile1.save(update_fields=["rating"])
        profile2 = BloggerProfile.objects.get(user=self.blogger2)
        profile2.rating = Decimal("2.00")
        profile2.save(update_fields=["rating"])
        r = self.client.get(reverse("web:blogger_catalog"), {"min_rating": "4"})
        pks = [p.pk for p in r.context["platforms"]]
        self.assertIn(self.p_insta.pk, pks)
        self.assertNotIn(self.p_telegram.pk, pks)

    def test_default_sort_by_rating(self):
        from apps.profiles.models import BloggerProfile
        profile1 = BloggerProfile.objects.get(user=self.blogger)
        profile1.rating = Decimal("3.00")
        profile1.save(update_fields=["rating"])
        profile2 = BloggerProfile.objects.get(user=self.blogger2)
        profile2.rating = Decimal("4.50")
        profile2.save(update_fields=["rating"])
        r = self.client.get(reverse("web:blogger_catalog"))
        platforms = list(r.context["platforms"])
        # Higher rating (blogger2/telegram) should come first
        self.assertEqual(platforms[0].pk, self.p_telegram.pk)
        self.assertEqual(platforms[1].pk, self.p_insta.pk)


class DirectOfferCreateTest(TestCase):
    """Advertiser creates a direct offer."""

    def setUp(self):
        self.advertiser = _make_user("adv@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("blogger@test.com", User.Role.BLOGGER)
        self.platform = _make_platform(self.blogger)
        self.campaign = _make_campaign(self.advertiser)
        w, _ = Wallet.objects.get_or_create(user=self.advertiser)
        w.available_balance = Decimal("500000")
        w.save(update_fields=["available_balance"])
        self.url = reverse("web:direct_offer_create", kwargs={"platform_pk": self.platform.pk})

    def test_get_shows_form(self):
        self.client.force_login(self.advertiser)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertIn("form", r.context)

    def test_blogger_cannot_access(self):
        self.client.force_login(self.blogger)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 302)

    def test_post_creates_direct_offer(self):
        self.client.force_login(self.advertiser)
        r = self.client.post(self.url, {
            "campaign": self.campaign.pk,
            "content_type": "post",
            "proposed_price": "50000",
            "message": "Привет, блогер!",
        })
        self.assertRedirects(r, reverse("web:blogger_catalog"))
        offer = DirectOffer.objects.get(advertiser=self.advertiser, platform=self.platform)
        self.assertEqual(offer.blogger, self.blogger)
        self.assertEqual(offer.status, DirectOffer.Status.PENDING)
        self.assertEqual(offer.proposed_price, Decimal("50000"))

    def test_404_for_pending_platform(self):
        pending_p = _make_platform(self.blogger, Platform.Status.PENDING)
        self.client.force_login(self.advertiser)
        r = self.client.get(reverse("web:direct_offer_create", kwargs={"platform_pk": pending_p.pk}))
        self.assertEqual(r.status_code, 404)

    def test_duplicate_offer_rejected(self):
        self.client.force_login(self.advertiser)
        DirectOffer.objects.create(
            advertiser=self.advertiser,
            blogger=self.blogger,
            campaign=self.campaign,
            platform=self.platform,
            content_type="post",
        )
        r = self.client.post(self.url, {
            "campaign": self.campaign.pk,
            "content_type": "post",
        })
        # stays on profile page with error
        self.assertEqual(r.status_code, 302)
        self.assertEqual(DirectOffer.objects.filter(advertiser=self.advertiser).count(), 1)


class DirectOfferAcceptTest(TestCase):
    """Blogger accepts a direct offer → Deal created."""

    def setUp(self):
        self.advertiser = _make_user("adv@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("blogger@test.com", User.Role.BLOGGER)
        self.platform = _make_platform(self.blogger)
        self.campaign = _make_campaign(self.advertiser)
        w, _ = Wallet.objects.get_or_create(user=self.advertiser)
        w.available_balance = Decimal("500000")
        w.save(update_fields=["available_balance"])
        Wallet.objects.get_or_create(user=self.blogger)
        self.offer = DirectOffer.objects.create(
            advertiser=self.advertiser,
            blogger=self.blogger,
            campaign=self.campaign,
            platform=self.platform,
            content_type="post",
            proposed_price=Decimal("50000"),
        )

    def test_accept_creates_deal(self):
        self.client.force_login(self.blogger)
        r = self.client.post(reverse("web:direct_offer_accept", kwargs={"pk": self.offer.pk}))
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.status, DirectOffer.Status.ACCEPTED)
        self.assertIsNotNone(self.offer.deal)
        deal = self.offer.deal
        self.assertEqual(deal.blogger, self.blogger)
        self.assertEqual(deal.advertiser, self.advertiser)
        self.assertEqual(deal.status, Deal.Status.IN_PROGRESS)

    def test_advertiser_cannot_accept_own_offer(self):
        self.client.force_login(self.advertiser)
        r = self.client.post(reverse("web:direct_offer_accept", kwargs={"pk": self.offer.pk}))
        self.assertEqual(r.status_code, 404)

    def test_cannot_accept_inactive_campaign(self):
        self.campaign.status = Campaign.Status.PAUSED
        self.campaign.save()
        self.client.force_login(self.blogger)
        self.client.post(reverse("web:direct_offer_accept", kwargs={"pk": self.offer.pk}))
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.status, DirectOffer.Status.PENDING)


class DirectOfferRejectTest(TestCase):
    """Blogger rejects a direct offer."""

    def setUp(self):
        self.advertiser = _make_user("adv@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("blogger@test.com", User.Role.BLOGGER)
        self.platform = _make_platform(self.blogger)
        self.campaign = _make_campaign(self.advertiser)
        self.offer = DirectOffer.objects.create(
            advertiser=self.advertiser,
            blogger=self.blogger,
            campaign=self.campaign,
            platform=self.platform,
            content_type="post",
        )

    def test_reject_sets_status(self):
        self.client.force_login(self.blogger)
        r = self.client.post(reverse("web:direct_offer_reject", kwargs={"pk": self.offer.pk}))
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.status, DirectOffer.Status.REJECTED)
        self.assertRedirects(r, reverse("web:blogger_dashboard"))

    def test_advertiser_cannot_reject(self):
        self.client.force_login(self.advertiser)
        r = self.client.post(reverse("web:direct_offer_reject", kwargs={"pk": self.offer.pk}))
        self.assertEqual(r.status_code, 404)


class BloggerDashboardOffersTest(TestCase):
    """Blogger dashboard shows incoming offers."""

    def setUp(self):
        self.advertiser = _make_user("adv@test.com", User.Role.ADVERTISER)
        self.blogger = _make_user("blogger@test.com", User.Role.BLOGGER)
        self.platform = _make_platform(self.blogger)
        self.campaign = _make_campaign(self.advertiser)
        Wallet.objects.get_or_create(user=self.blogger)

    def test_no_offers_by_default(self):
        self.client.force_login(self.blogger)
        r = self.client.get(reverse("web:blogger_dashboard"))
        self.assertEqual(list(r.context["incoming_offers"]), [])

    def test_pending_offer_shown(self):
        offer = DirectOffer.objects.create(
            advertiser=self.advertiser,
            blogger=self.blogger,
            campaign=self.campaign,
            platform=self.platform,
            content_type="post",
        )
        self.client.force_login(self.blogger)
        r = self.client.get(reverse("web:blogger_dashboard"))
        self.assertIn(offer, r.context["incoming_offers"])

    def test_rejected_offer_not_shown(self):
        DirectOffer.objects.create(
            advertiser=self.advertiser,
            blogger=self.blogger,
            campaign=self.campaign,
            platform=self.platform,
            content_type="post",
            status=DirectOffer.Status.REJECTED,
        )
        self.client.force_login(self.blogger)
        r = self.client.get(reverse("web:blogger_dashboard"))
        self.assertEqual(list(r.context["incoming_offers"]), [])
