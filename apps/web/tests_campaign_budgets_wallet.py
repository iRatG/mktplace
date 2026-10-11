"""
#53: кошелёк рекламодателя показывает разбивку бюджета по кампаниям (в офертах / в сделках / выплачено / осталось),
итог и нехватку свободных денег; страница кампании — ту же разбивку. Деньги под бюджет не замораживаются.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import Wallet
from apps.campaigns.models import Campaign
from apps.campaigns.models import Response as CampaignResponse
from apps.campaigns.services import accept_response
from apps.campaigns.testing import deal_from_response
from apps.campaigns.validation import advertiser_budgets, budget_committed, budget_remaining, budget_usage
from apps.deals.models import Deal
from apps.platforms.models import Platform
from apps.users.models import User

_n = 0


class CampaignBudgetsWalletTest(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.adv = User.objects.create_user(email="adv@test.com", password="pass1234", role=User.Role.ADVERTISER,
                                            status=User.Status.ACTIVE)
        Wallet.objects.filter(user=self.adv).update(available_balance=Decimal("10000000"))
        self.blogger = User.objects.create_user(email="bl@test.com", password="pass1234", role=User.Role.BLOGGER,
                                                status=User.Status.ACTIVE)
        self.a = self._campaign("Клиент А", "1000000")
        self.b = self._campaign("Клиент Б", "500000")

    def _campaign(self, name, budget, status=Campaign.Status.ACTIVE):
        return Campaign.objects.create(
            advertiser=self.adv, name=name, payment_type=Campaign.PaymentType.FIXED, fixed_price=Decimal("100000"),
            budget=Decimal(budget), status=status, start_date=self.today, end_date=self.today + timedelta(days=30),
            approval_required=False,
        )

    def _response(self, campaign, price):
        global _n
        _n += 1
        platform = Platform.objects.create(
            blogger=self.blogger, social_type=Platform.SocialType.INSTAGRAM, url=f"https://instagram.com/w{_n}",
            subscribers=12000, price_post=Decimal("50000"), status=Platform.Status.APPROVED,
        )
        return CampaignResponse.objects.create(campaign=campaign, blogger=self.blogger, platform=platform,
                                               content_type="post", proposed_price=Decimal(price))

    def _fill(self):
        """А: оферта 100 000, сделка в работе 200 000, завершённая 150 000. Б: сделка в работе 120 000."""
        day = self.today + timedelta(days=2)
        accept_response(self._response(self.a, "100000").pk, self.adv, day)
        deal_from_response(self._response(self.a, "200000"), self.adv, publication_date=day)
        done = deal_from_response(self._response(self.a, "150000"), self.adv, publication_date=day)
        Deal.objects.filter(pk=done.pk).update(status=Deal.Status.COMPLETED)
        deal_from_response(self._response(self.b, "120000"), self.adv, publication_date=day)

    def test_usage_split_matches_committed(self):
        self._fill()
        usage = budget_usage([self.a, self.b])
        a = usage[self.a.pk]
        self.assertEqual((a["offers"], a["deals"], a["paid"]), (Decimal("100000"), Decimal("200000"), Decimal("150000")))
        self.assertEqual(a["offers"] + a["deals"] + a["paid"], budget_committed(self.a))
        self.assertEqual(a["remaining"], budget_remaining(self.a))
        self.assertEqual(usage[self.b.pk]["remaining"], Decimal("380000"))

    def test_wallet_block_with_total(self):
        self._fill()
        self._campaign("Черновик", "999000", status=Campaign.Status.DRAFT)  # черновики не показываются
        self.client.force_login(self.adv)
        page = self.client.get(reverse("web:wallet")).content.decode()
        self.assertIn("data-campaign-budgets", page)
        self.assertIn("Клиент А", page)
        self.assertNotIn("Черновик", page)
        total = page[page.index("data-budget-total"):]
        self.assertIn("1 500 000", total)  # бюджеты
        self.assertIn("930 000", total)  # осталось: А 1 000 000 − 450 000 + Б 500 000 − 120 000
        self.assertNotIn("data-budget-shortage", page)

    def test_shortage_warning_counts_commission(self):
        Wallet.objects.filter(user=self.adv).update(available_balance=Decimal("1000000"))
        budgets = advertiser_budgets(self.adv)
        # остатки 1 500 000 + комиссия 16% = 1 740 000, свободно 1 000 000
        self.assertEqual(budgets["need"], Decimal("1740000.00"))
        self.assertEqual(budgets["shortage"], Decimal("740000.00"))
        self.client.force_login(self.adv)
        page = self.client.get(reverse("web:wallet")).content.decode()
        self.assertIn("data-budget-shortage", page)
        self.assertIn("не хватает 740 000", page)

    def test_blogger_wallet_has_no_block(self):
        self.client.force_login(self.blogger)
        self.assertNotContains(self.client.get(reverse("web:wallet")), "data-campaign-budgets")

    def test_campaign_page_line(self):
        self._fill()
        self.client.force_login(self.adv)
        page = self.client.get(reverse("web:campaign_detail", kwargs={"pk": self.a.pk}))
        self.assertContains(page, "в офертах 100 000 · в сделках 200 000 · выплачено 150 000")

    def test_wallet_queries_do_not_grow_with_campaigns(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.client.force_login(self.adv)
        self.client.get(reverse("web:wallet"))
        with CaptureQueriesContext(connection) as one:
            self.client.get(reverse("web:wallet"))
        for i in range(3):
            self._campaign(f"Ещё {i}", "300000")
        with CaptureQueriesContext(connection) as two:
            self.client.get(reverse("web:wallet"))
        self.assertEqual(len(one), len(two))
