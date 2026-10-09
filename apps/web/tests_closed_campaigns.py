"""
#43: открытая и закрытая кампания. Закрытую видят и могут откликнуться только приглашённые исполнители;
приглашение — по одному (из каталога) или по критериям; приглашение не оферта.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.campaigns.models import Campaign, CampaignInvitation, DirectOffer
from apps.campaigns.models import Response as CampaignResponse
from apps.campaigns.services import AcceptError, invite_bloggers, invite_by_criteria
from apps.campaigns.testing import CARD_FIELDS
from apps.campaigns.validation import campaigns_visible_to
from apps.notifications.models import Notification
from apps.platforms.models import Platform
from apps.users.models import User

_n = 0


def _blogger(email, subscribers=12000, social=Platform.SocialType.INSTAGRAM):
    global _n
    _n += 1
    user = User.objects.create_user(email=email, password="pass1234", role=User.Role.BLOGGER, status=User.Status.ACTIVE)
    Platform.objects.create(blogger=user, social_type=social, url=f"https://instagram.com/cc{_n}",
                            subscribers=subscribers, price_post=Decimal("50000"), status=Platform.Status.APPROVED)
    return user


class ClosedCampaignTest(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.adv = User.objects.create_user(email="adv@test.com", password="pass1234", role=User.Role.ADVERTISER,
                                            status=User.Status.ACTIVE)
        self.campaign = Campaign.objects.create(
            advertiser=self.adv, name="Закрытая", payment_type=Campaign.PaymentType.FIXED,
            fixed_price=Decimal("100000"), budget=Decimal("1000000"), status=Campaign.Status.ACTIVE,
            start_date=today, end_date=today + timedelta(days=30), visibility=Campaign.Visibility.CLOSED,
            **CARD_FIELDS,
        )
        self.invited = _blogger("in@test.com")
        self.stranger = _blogger("out@test.com", subscribers=100, social=Platform.SocialType.TELEGRAM)

    def test_only_invited_see_and_respond(self):
        invite_bloggers(self.campaign.pk, self.adv, [self.invited])
        self.assertIn(self.campaign, campaigns_visible_to(self.invited))
        self.assertNotIn(self.campaign, campaigns_visible_to(self.stranger))

        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk})).status_code, 404)
        self.assertNotIn("Закрытая", self.client.get(reverse("web:campaign_list")).content.decode())

        self.client.force_login(self.invited)
        self.assertEqual(self.client.get(reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk})).status_code, 200)
        self.assertIn("Закрытая", self.client.get(reverse("web:campaign_list")).content.decode())
        platform = Platform.objects.get(blogger=self.invited)
        self.client.post(reverse("web:campaign_respond", kwargs={"pk": self.campaign.pk}),
                         {"platform": platform.pk, "content_type": "post"})
        self.assertTrue(CampaignResponse.objects.filter(campaign=self.campaign, blogger=self.invited).exists())

    def test_api_hides_and_rejects_not_invited(self):
        api = APIClient()
        api.force_authenticate(self.stranger)
        data = api.get("/api/v1/campaigns/").json()
        ids = [c["id"] for c in (data["results"] if isinstance(data, dict) else data)]
        self.assertNotIn(self.campaign.pk, ids)
        platform = Platform.objects.get(blogger=self.stranger)
        r = api.post(reverse("campaigns:response-list"), {"campaign": self.campaign.pk, "platform": platform.pk, "content_type": "post"})
        self.assertEqual(r.status_code, 400)

    def test_invitation_is_not_an_offer_and_notifies(self):
        self.assertEqual(invite_bloggers(self.campaign.pk, self.adv, [self.invited]), 1)
        self.assertEqual(invite_bloggers(self.campaign.pk, self.adv, [self.invited]), 0)  # повтор пропускается
        self.assertFalse(DirectOffer.objects.exists())
        self.assertTrue(Notification.objects.filter(user=self.invited, title="Приглашение в закрытую кампанию").exists())
        self.client.force_login(self.invited)
        self.assertIn("data-invitations", self.client.get(reverse("web:blogger_dashboard")).content.decode())

    def test_invite_by_criteria(self):
        count = invite_by_criteria(self.campaign.pk, self.adv, social_type="instagram", min_subscribers=1000)
        self.assertEqual(count, 1)
        self.assertEqual(list(CampaignInvitation.objects.values_list("blogger__email", flat=True)), ["in@test.com"])

    def test_open_campaign_needs_no_invitation(self):
        Campaign.objects.filter(pk=self.campaign.pk).update(visibility=Campaign.Visibility.OPEN)
        self.assertIn(self.campaign, campaigns_visible_to(self.stranger))
        with self.assertRaisesMessage(AcceptError, "только для закрытой"):
            invite_bloggers(self.campaign.pk, self.adv, [self.stranger])

    def test_web_invite_from_catalog_and_page(self):
        self.client.force_login(self.adv)
        catalog = self.client.get(reverse("web:blogger_catalog")).content.decode()
        self.assertIn("data-invite-one", catalog)
        self.client.post(reverse("web:campaign_invite", kwargs={"pk": self.campaign.pk}), {"blogger": self.stranger.pk})
        self.assertTrue(CampaignInvitation.objects.filter(blogger=self.stranger).exists())
        page = self.client.get(reverse("web:campaign_detail", kwargs={"pk": self.campaign.pk})).content.decode()
        self.assertIn("data-invitations", page)
        self.client.post(reverse("web:campaign_invite", kwargs={"pk": self.campaign.pk}), {"social_type": "instagram"})
        self.assertEqual(CampaignInvitation.objects.count(), 2)

    def test_form_has_visibility_default_open(self):
        self.client.force_login(self.adv)
        page = self.client.get(reverse("web:campaign_create")).content.decode()
        self.assertIn('name="visibility"', page)
        from apps.web.forms import CampaignForm

        form = CampaignForm(data={"name": "Н", "payment_type": "fixed", "fixed_price": "150000", "budget": "1500000",
                                  "min_subscribers": "0", "max_bloggers": "0"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["visibility"], "open")
