"""
Synthetic tests for profile module.
Covers: signal auto-create, profile view/edit, platform CRUD, public profile, security.

Run: docker compose run --rm web python manage.py test apps.web.tests_profiles -v 2
"""
from django.test import TestCase, Client
from django.urls import reverse

from apps.platforms.models import Category, Platform
from apps.profiles.models import AdvertiserProfile, BloggerProfile
from apps.users.models import User


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_blogger(email="blogger@test.com", confirmed=True):
    u = User.objects.create_user(email=email, password="Test1234!", role=User.Role.BLOGGER)
    u.status = User.Status.ACTIVE
    u.is_email_confirmed = confirmed
    u.save()
    return u


def make_advertiser(email="adv@test.com"):
    u = User.objects.create_user(email=email, password="Test1234!", role=User.Role.ADVERTISER)
    u.status = User.Status.ACTIVE
    u.is_email_confirmed = True
    u.save()
    return u


def make_staff(email="admin@test.com"):
    u = User.objects.create_user(email=email, password="Test1234!", role=User.Role.ADVERTISER)
    u.status = User.Status.ACTIVE
    u.is_email_confirmed = True
    u.is_staff = True
    u.save()
    return u


def make_platform(blogger, status=Platform.Status.APPROVED, url="https://t.me/testchan"):
    return Platform.objects.create(
        blogger=blogger,
        social_type=Platform.SocialType.TELEGRAM,
        url=url,
        subscribers=1000,
        status=status,
    )


# ── 1. Signal: auto-create profile on registration ───────────────────────────

class SignalAutoCreateProfileTest(TestCase):

    def test_blogger_profile_created_on_register(self):
        u = make_blogger("b1@test.com")
        self.assertTrue(BloggerProfile.objects.filter(user=u).exists())

    def test_advertiser_profile_created_on_register(self):
        u = make_advertiser("a1@test.com")
        self.assertTrue(AdvertiserProfile.objects.filter(user=u).exists())

    def test_blogger_has_no_advertiser_profile(self):
        u = make_blogger("b2@test.com")
        self.assertFalse(AdvertiserProfile.objects.filter(user=u).exists())

    def test_advertiser_has_no_blogger_profile(self):
        u = make_advertiser("a2@test.com")
        self.assertFalse(BloggerProfile.objects.filter(user=u).exists())

    def test_profile_not_created_on_update(self):
        u = make_blogger("b3@test.com")
        BloggerProfile.objects.filter(user=u).delete()
        # Update should NOT recreate
        u.status = User.Status.ACTIVE
        u.save()
        self.assertFalse(BloggerProfile.objects.filter(user=u).exists())


# ── 2. check_completeness logic ───────────────────────────────────────────────

class ProfileCompletenessTest(TestCase):

    def test_blogger_incomplete_when_empty(self):
        u = make_blogger("b@test.com")
        p = BloggerProfile.objects.get(user=u)
        result = p.check_completeness()
        self.assertFalse(result)
        self.assertFalse(p.is_complete)

    def test_blogger_complete_when_nickname_and_bio_filled(self):
        u = make_blogger("b@test.com")
        p = BloggerProfile.objects.get(user=u)
        p.nickname = "Vasya"
        p.bio = "I am a blogger"
        p.save()
        result = p.check_completeness()
        self.assertTrue(result)
        self.assertTrue(p.is_complete)

    def test_blogger_incomplete_when_only_nickname(self):
        u = make_blogger("b@test.com")
        p = BloggerProfile.objects.get(user=u)
        p.nickname = "Vasya"
        p.save()
        result = p.check_completeness()
        self.assertFalse(result)

    def test_advertiser_complete_when_all_required_filled(self):
        u = make_advertiser("a@test.com")
        p = AdvertiserProfile.objects.get(user=u)
        p.company_name = "ООО Ромашка"
        p.industry = "IT"
        p.contact_name = "Иван"
        p.phone = "+998901234567"
        p.save()
        result = p.check_completeness()
        self.assertTrue(result)

    def test_advertiser_incomplete_when_phone_missing(self):
        u = make_advertiser("a@test.com")
        p = AdvertiserProfile.objects.get(user=u)
        p.company_name = "ООО Ромашка"
        p.industry = "IT"
        p.contact_name = "Иван"
        p.save()
        result = p.check_completeness()
        self.assertFalse(result)


# ── 3. /profile/ view ─────────────────────────────────────────────────────────

class ProfileViewTest(TestCase):

    def test_anonymous_redirected_to_login(self):
        c = Client()
        r = c.get(reverse("web:profile"))
        self.assertRedirects(r, "/login/?next=/profile/", fetch_redirect_response=False)

    def test_blogger_sees_own_profile(self):
        u = make_blogger()
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:profile"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Мой профиль")

    def test_advertiser_sees_own_profile(self):
        u = make_advertiser()
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:profile"))
        self.assertEqual(r.status_code, 200)

    def test_staff_redirected_to_admin_dashboard(self):
        u = make_staff()
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:profile"))
        self.assertRedirects(r, reverse("web:admin_dashboard"), fetch_redirect_response=False)

    def test_blogger_profile_shows_platforms(self):
        u = make_blogger()
        make_platform(u, url="https://t.me/mychan")
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:profile"))
        self.assertContains(r, "https://t.me/mychan")


# ── 4. /profile/edit/ ────────────────────────────────────────────────────────

class ProfileEditTest(TestCase):

    def test_anonymous_redirected(self):
        c = Client()
        r = c.get(reverse("web:profile_edit"))
        self.assertEqual(r.status_code, 302)

    def test_staff_redirected(self):
        u = make_staff()
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:profile_edit"))
        self.assertRedirects(r, reverse("web:admin_dashboard"), fetch_redirect_response=False)

    def test_blogger_edit_get_shows_form(self):
        u = make_blogger()
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:profile_edit"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "nickname")

    def test_blogger_edit_post_updates_profile(self):
        u = make_blogger()
        c = Client()
        c.force_login(u)
        c.post(reverse("web:profile_edit"), {"nickname": "Vasya", "bio": "Hello world"})
        p = BloggerProfile.objects.get(user=u)
        self.assertEqual(p.nickname, "Vasya")
        self.assertEqual(p.bio, "Hello world")

    def test_blogger_edit_post_sets_is_complete_true_when_filled(self):
        u = make_blogger()
        c = Client()
        c.force_login(u)
        c.post(reverse("web:profile_edit"), {"nickname": "Vasya", "bio": "Hello"})
        p = BloggerProfile.objects.get(user=u)
        self.assertTrue(p.is_complete)

    def test_blogger_edit_post_is_complete_false_when_empty(self):
        u = make_blogger()
        c = Client()
        c.force_login(u)
        c.post(reverse("web:profile_edit"), {"nickname": "", "bio": ""})
        p = BloggerProfile.objects.get(user=u)
        self.assertFalse(p.is_complete)

    def test_advertiser_edit_post_updates_profile(self):
        u = make_advertiser()
        c = Client()
        c.force_login(u)
        c.post(reverse("web:profile_edit"), {
            "company_name": "ООО Тест",
            "industry": "IT",
            "contact_name": "Иван",
            "phone": "+998900000000",
            "website": "",
            "description": "",
        })
        p = AdvertiserProfile.objects.get(user=u)
        self.assertEqual(p.company_name, "ООО Тест")
        self.assertTrue(p.is_complete)

    def test_advertiser_edit_incomplete_without_phone(self):
        u = make_advertiser()
        c = Client()
        c.force_login(u)
        c.post(reverse("web:profile_edit"), {
            "company_name": "ООО Тест",
            "industry": "IT",
            "contact_name": "Иван",
            "phone": "",
            "website": "",
            "description": "",
        })
        p = AdvertiserProfile.objects.get(user=u)
        self.assertFalse(p.is_complete)


# ── 5. Platform add ───────────────────────────────────────────────────────────

class PlatformAddTest(TestCase):

    def test_advertiser_redirected_from_platform_add(self):
        u = make_advertiser()
        c = Client()
        c.force_login(u)
        r = c.post(reverse("web:platform_add"), {})
        self.assertEqual(r.status_code, 302)
        self.assertNotEqual(r["Location"], reverse("web:profile"))

    def test_blogger_can_add_platform(self):
        u = make_blogger()
        c = Client()
        c.force_login(u)
        r = c.post(reverse("web:platform_add"), {
            "social_type": "telegram",
            "url": "https://t.me/newchan",
            "subscribers": 5000,
            "avg_views": 1000,
            "engagement_rate": "3.50",
        })
        self.assertRedirects(r, reverse("web:profile"), fetch_redirect_response=False)
        self.assertTrue(Platform.objects.filter(blogger=u, url="https://t.me/newchan").exists())

    def test_new_platform_has_pending_status(self):
        u = make_blogger()
        c = Client()
        c.force_login(u)
        c.post(reverse("web:platform_add"), {
            "social_type": "telegram",
            "url": "https://t.me/newchan",
            "subscribers": 5000,
            "avg_views": 1000,
            "engagement_rate": "3.50",
        })
        p = Platform.objects.get(blogger=u)
        self.assertEqual(p.status, Platform.Status.PENDING)

    def test_anonymous_cannot_add_platform(self):
        c = Client()
        r = c.post(reverse("web:platform_add"), {"social_type": "telegram", "url": "https://t.me/x"})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Platform.objects.filter(url="https://t.me/x").exists())


# ── 6. Platform edit ──────────────────────────────────────────────────────────

class PlatformEditTest(TestCase):

    def test_blogger_can_edit_own_platform(self):
        u = make_blogger()
        p = make_platform(u, status=Platform.Status.PENDING)
        c = Client()
        c.force_login(u)
        r = c.post(reverse("web:platform_edit", args=[p.pk]), {
            "social_type": "telegram",
            "url": "https://t.me/testchan",  # same URL
            "subscribers": 9999,
            "avg_views": 500,
            "engagement_rate": "5.00",
        })
        self.assertRedirects(r, reverse("web:profile"), fetch_redirect_response=False)
        p.refresh_from_db()
        self.assertEqual(p.subscribers, 9999)

    def test_blogger_cannot_edit_another_bloggers_platform(self):
        owner = make_blogger("owner@test.com")
        other = make_blogger("other@test.com")
        p = make_platform(owner)
        c = Client()
        c.force_login(other)
        r = c.post(reverse("web:platform_edit", args=[p.pk]), {
            "social_type": "telegram",
            "url": "https://t.me/hacked",
            "subscribers": 1,
            "avg_views": 1,
            "engagement_rate": "1.00",
        })
        self.assertEqual(r.status_code, 404)

    def test_url_change_on_approved_platform_triggers_re_moderation(self):
        u = make_blogger()
        p = make_platform(u, status=Platform.Status.APPROVED, url="https://t.me/original")
        c = Client()
        c.force_login(u)
        c.post(reverse("web:platform_edit", args=[p.pk]), {
            "social_type": "telegram",
            "url": "https://t.me/CHANGED",
            "subscribers": 1000,
            "avg_views": 500,
            "engagement_rate": "3.00",
        })
        p.refresh_from_db()
        self.assertEqual(p.status, Platform.Status.PENDING)

    def test_stats_change_on_approved_platform_keeps_approved_status(self):
        u = make_blogger()
        p = make_platform(u, status=Platform.Status.APPROVED, url="https://t.me/testchan")
        c = Client()
        c.force_login(u)
        c.post(reverse("web:platform_edit", args=[p.pk]), {
            "social_type": "telegram",
            "url": "https://t.me/testchan",  # same URL
            "subscribers": 99999,  # only stats changed
            "avg_views": 500,
            "engagement_rate": "7.00",
        })
        p.refresh_from_db()
        self.assertEqual(p.status, Platform.Status.APPROVED)

    def test_url_change_on_pending_platform_stays_pending(self):
        u = make_blogger()
        p = make_platform(u, status=Platform.Status.PENDING, url="https://t.me/orig")
        c = Client()
        c.force_login(u)
        c.post(reverse("web:platform_edit", args=[p.pk]), {
            "social_type": "telegram",
            "url": "https://t.me/changed",
            "subscribers": 1000,
            "avg_views": 500,
            "engagement_rate": "3.00",
        })
        p.refresh_from_db()
        self.assertEqual(p.status, Platform.Status.PENDING)


# ── 7. Platform delete ────────────────────────────────────────────────────────

class PlatformDeleteTest(TestCase):

    def test_can_delete_pending_platform(self):
        u = make_blogger()
        p = make_platform(u, status=Platform.Status.PENDING)
        c = Client()
        c.force_login(u)
        c.post(reverse("web:platform_delete", args=[p.pk]))
        self.assertFalse(Platform.objects.filter(pk=p.pk).exists())

    def test_can_delete_rejected_platform(self):
        u = make_blogger()
        p = make_platform(u, status=Platform.Status.REJECTED)
        c = Client()
        c.force_login(u)
        c.post(reverse("web:platform_delete", args=[p.pk]))
        self.assertFalse(Platform.objects.filter(pk=p.pk).exists())

    def test_cannot_delete_approved_platform(self):
        u = make_blogger()
        p = make_platform(u, status=Platform.Status.APPROVED)
        c = Client()
        c.force_login(u)
        c.post(reverse("web:platform_delete", args=[p.pk]))
        self.assertTrue(Platform.objects.filter(pk=p.pk).exists())

    def test_cannot_delete_another_bloggers_platform(self):
        owner = make_blogger("owner@test.com")
        other = make_blogger("other@test.com")
        p = make_platform(owner, status=Platform.Status.PENDING)
        c = Client()
        c.force_login(other)
        r = c.post(reverse("web:platform_delete", args=[p.pk]))
        self.assertEqual(r.status_code, 404)

    def test_get_method_not_allowed_on_delete(self):
        u = make_blogger()
        p = make_platform(u, status=Platform.Status.PENDING)
        c = Client()
        c.force_login(u)
        r = c.get(reverse("web:platform_delete", args=[p.pk]))
        self.assertEqual(r.status_code, 405)


# ── 8. Public blogger profile ─────────────────────────────────────────────────

class BloggerPublicProfileTest(TestCase):

    def test_anonymous_redirected_to_login(self):
        u = make_blogger()
        c = Client()
        r = c.get(reverse("web:blogger_public_profile", args=[u.pk]))
        self.assertEqual(r.status_code, 302)

    def test_authenticated_user_can_view_profile(self):
        blogger = make_blogger("blogger@test.com")
        viewer = make_advertiser("viewer@test.com")
        c = Client()
        c.force_login(viewer)
        r = c.get(reverse("web:blogger_public_profile", args=[blogger.pk]))
        self.assertEqual(r.status_code, 200)

    def test_non_blogger_user_pk_returns_404(self):
        adv = make_advertiser()
        viewer = make_advertiser("viewer@test.com")
        c = Client()
        c.force_login(viewer)
        r = c.get(reverse("web:blogger_public_profile", args=[adv.pk]))
        self.assertEqual(r.status_code, 404)

    def test_only_approved_platforms_shown(self):
        blogger = make_blogger()
        make_platform(blogger, status=Platform.Status.APPROVED, url="https://t.me/approved")
        make_platform(blogger, status=Platform.Status.PENDING, url="https://t.me/pending")
        make_platform(blogger, status=Platform.Status.REJECTED, url="https://t.me/rejected")
        viewer = make_advertiser()
        c = Client()
        c.force_login(viewer)
        r = c.get(reverse("web:blogger_public_profile", args=[blogger.pk]))
        self.assertContains(r, "https://t.me/approved")
        self.assertNotContains(r, "https://t.me/pending")
        self.assertNotContains(r, "https://t.me/rejected")

    def test_shows_completed_deals_count(self):
        blogger = make_blogger()
        viewer = make_advertiser()
        c = Client()
        c.force_login(viewer)
        r = c.get(reverse("web:blogger_public_profile", args=[blogger.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertIn("completed_deals", r.context)
        self.assertEqual(r.context["completed_deals"], 0)

    def test_blogger_can_view_other_blogger_profile(self):
        b1 = make_blogger("b1@test.com")
        b2 = make_blogger("b2@test.com")
        c = Client()
        c.force_login(b1)
        r = c.get(reverse("web:blogger_public_profile", args=[b2.pk]))
        self.assertEqual(r.status_code, 200)
