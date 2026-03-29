from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Sum
from django.shortcuts import redirect, render

from apps.billing.models import Transaction
from apps.campaigns.models import Campaign
from apps.campaigns.models import Response as CampaignResponse
from apps.deals.models import Deal
from apps.profiles.models import BloggerProfile
from apps.users.models import User


@login_required
def analytics_view(request):
    """Аналитика: маршрутизирует по роли на соответствующий шаблон."""
    user = request.user
    if user.is_staff:
        return redirect("web:admin_dashboard")

    if user.role == User.Role.ADVERTISER:
        return _analytics_advertiser(request, user)
    return _analytics_blogger(request, user)


def _analytics_advertiser(request, user):
    """Аналитический дашборд для рекламодателя."""
    deals_qs = Deal.objects.filter(advertiser=user)
    total_deals = deals_qs.count()
    completed_deals = deals_qs.filter(status=Deal.Status.COMPLETED).count()
    cancelled_deals = deals_qs.filter(status=Deal.Status.CANCELLED).count()
    active_deals = deals_qs.exclude(
        status__in=[Deal.Status.COMPLETED, Deal.Status.CANCELLED]
    ).count()
    completion_rate = round(completed_deals / total_deals * 100) if total_deals else 0

    avg_deal = (
        deals_qs.filter(status=Deal.Status.COMPLETED)
        .aggregate(avg=Avg("amount"))["avg"]
        or Decimal("0")
    )

    total_spent = (
        Transaction.objects.filter(wallet__user=user, type=Transaction.Type.PAYMENT)
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )
    total_deposited = (
        Transaction.objects.filter(wallet__user=user, type=Transaction.Type.DEPOSIT)
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )

    campaigns_qs = Campaign.objects.filter(advertiser=user)
    campaigns_by_status = {
        "active": campaigns_qs.filter(status=Campaign.Status.ACTIVE).count(),
        "completed": campaigns_qs.filter(status=Campaign.Status.COMPLETED).count(),
        "draft": campaigns_qs.filter(status=Campaign.Status.DRAFT).count(),
        "paused": campaigns_qs.filter(status=Campaign.Status.PAUSED).count(),
    }

    recent_completed = (
        deals_qs.filter(status=Deal.Status.COMPLETED)
        .select_related("blogger", "campaign")
        .order_by("-updated_at")[:5]
    )

    context = {
        "total_deals": total_deals,
        "completed_deals": completed_deals,
        "cancelled_deals": cancelled_deals,
        "active_deals": active_deals,
        "completion_rate": completion_rate,
        "avg_deal": avg_deal,
        "total_spent": total_spent,
        "total_deposited": total_deposited,
        "campaigns_by_status": campaigns_by_status,
        "recent_completed": recent_completed,
    }
    return render(request, "analytics/advertiser.html", context)


def _analytics_blogger(request, user):
    """Аналитический дашборд для блогера."""
    deals_qs = Deal.objects.filter(blogger=user)
    total_deals = deals_qs.count()
    completed_deals = deals_qs.filter(status=Deal.Status.COMPLETED).count()
    cancelled_deals = deals_qs.filter(status=Deal.Status.CANCELLED).count()
    active_deals = deals_qs.exclude(
        status__in=[Deal.Status.COMPLETED, Deal.Status.CANCELLED]
    ).count()
    completion_rate = round(completed_deals / total_deals * 100) if total_deals else 0

    avg_earning = (
        deals_qs.filter(status=Deal.Status.COMPLETED)
        .aggregate(avg=Avg("amount"))["avg"]
        or Decimal("0")
    )
    total_earned = (
        Transaction.objects.filter(wallet__user=user, type=Transaction.Type.EARNING)
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )

    total_responses = CampaignResponse.objects.filter(blogger=user).count()
    accepted_responses = CampaignResponse.objects.filter(
        blogger=user, status=CampaignResponse.Status.ACCEPTED
    ).count()
    acceptance_rate = (
        round(accepted_responses / total_responses * 100) if total_responses else 0
    )

    profile, _ = BloggerProfile.objects.get_or_create(user=user)

    recent_completed = (
        deals_qs.filter(status=Deal.Status.COMPLETED)
        .select_related("campaign")
        .order_by("-updated_at")[:5]
    )

    context = {
        "total_deals": total_deals,
        "completed_deals": completed_deals,
        "cancelled_deals": cancelled_deals,
        "active_deals": active_deals,
        "completion_rate": completion_rate,
        "avg_earning": avg_earning,
        "total_earned": total_earned,
        "total_responses": total_responses,
        "accepted_responses": accepted_responses,
        "acceptance_rate": acceptance_rate,
        "rating": profile.rating,
        "recent_completed": recent_completed,
    }
    return render(request, "analytics/blogger.html", context)
