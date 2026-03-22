from django.urls import path

from . import views

app_name = "web"

urlpatterns = [
    # Landing & static
    path("", views.landing, name="landing"),
    path("faq/", views.faq, name="faq"),

    # Auth
    path("login/", views.login_view, name="login"),
    path("register/", views.register_view, name="register"),
    path("logout/", views.logout_view, name="logout"),
    path("confirm-email/<uuid:token>/", views.email_confirm_view, name="email_confirm"),
    path("password-reset/", views.password_reset_request_view, name="password_reset"),
    path("password-reset/<uuid:token>/", views.password_reset_confirm_view, name="password_reset_confirm"),

    # Dashboards
    path("dashboard/advertiser/", views.advertiser_dashboard, name="advertiser_dashboard"),
    path("dashboard/blogger/", views.blogger_dashboard, name="blogger_dashboard"),

    # Campaigns
    path("campaigns/", views.campaign_list, name="campaign_list"),
    path("campaigns/create/", views.campaign_create, name="campaign_create"),
    path("campaigns/<int:pk>/", views.campaign_detail, name="campaign_detail"),
    path("campaigns/<int:pk>/edit/", views.campaign_edit, name="campaign_edit"),
    path("campaigns/<int:pk>/submit/", views.campaign_submit, name="campaign_submit"),
    path("campaigns/<int:pk>/pause/", views.campaign_pause, name="campaign_pause"),
    path("campaigns/<int:pk>/resume/", views.campaign_resume, name="campaign_resume"),
    path("campaigns/<int:pk>/respond/", views.campaign_respond, name="campaign_respond"),

    # Catalog (campaigns for bloggers)
    path("catalog/", views.campaign_list, name="catalog"),

    # Blogger catalog (platforms for advertisers) — Module 10
    path("bloggers/", views.blogger_catalog, name="blogger_catalog"),
    path("bloggers/<int:platform_pk>/offer/", views.direct_offer_create, name="direct_offer_create"),
    path("offers/<int:pk>/accept/", views.direct_offer_accept, name="direct_offer_accept"),
    path("offers/<int:pk>/reject/", views.direct_offer_reject, name="direct_offer_reject"),

    # Responses
    path("responses/<int:pk>/accept/", views.response_accept, name="response_accept"),
    path("responses/<int:pk>/reject/", views.response_reject, name="response_reject"),

    # Platforms
    path("platforms/add/", views.platform_add, name="platform_add"),
    path("platforms/<int:pk>/edit/", views.platform_edit, name="platform_edit"),
    path("platforms/<int:pk>/delete/", views.platform_delete, name="platform_delete"),

    # Profiles
    path("profile/", views.profile_view, name="profile"),
    path("profile/edit/", views.profile_edit, name="profile_edit"),
    path("bloggers/<int:pk>/", views.blogger_public_profile, name="blogger_public_profile"),

    # Deals
    path("deals/", views.deal_list, name="deal_list"),
    path("deals/<int:pk>/", views.deal_detail, name="deal_detail"),
    path("deals/<int:pk>/submit-publication/", views.deal_submit_publication, name="deal_submit_publication"),
    path("deals/<int:pk>/confirm/", views.deal_confirm, name="deal_confirm"),
    path("deals/<int:pk>/cancel/", views.deal_cancel, name="deal_cancel"),

    # Billing
    path("wallet/", views.wallet_view, name="wallet"),

    # Admin panel (staff only)
    path("panel/", views.admin_dashboard, name="admin_dashboard"),
    path("panel/campaigns/", views.admin_campaigns, name="admin_campaigns"),
    path("panel/campaigns/<int:pk>/approve/", views.admin_campaign_approve, name="admin_campaign_approve"),
    path("panel/campaigns/<int:pk>/reject/", views.admin_campaign_reject, name="admin_campaign_reject"),
    path("panel/platforms/", views.admin_platforms, name="admin_platforms"),
    path("panel/platforms/<int:pk>/approve/", views.admin_platform_approve, name="admin_platform_approve"),
    path("panel/platforms/<int:pk>/reject/", views.admin_platform_reject, name="admin_platform_reject"),
    path("panel/disputes/", views.admin_disputes, name="admin_disputes"),
    path("panel/disputes/<int:pk>/resolve/", views.admin_dispute_resolve, name="admin_dispute_resolve"),
    path("panel/withdrawals/", views.admin_withdrawals, name="admin_withdrawals"),
    path("panel/withdrawals/<int:pk>/approve/", views.admin_withdrawal_approve, name="admin_withdrawal_approve"),
    path("panel/withdrawals/<int:pk>/reject/", views.admin_withdrawal_reject, name="admin_withdrawal_reject"),
    path("panel/users/", views.admin_users, name="admin_users"),

    # Notifications (Module 11)
    path("notifications/", views.notification_list, name="notifications"),
    path("notifications/mark-all-read/", views.notification_mark_all_read, name="notifications_mark_all_read"),

    # Reviews (Module 7)
    path("deals/<int:pk>/review/", views.deal_review_submit, name="deal_review_submit"),

    # Admin: user management (Module 13)
    path("panel/users/<int:pk>/block/", views.admin_user_block, name="admin_user_block"),
    path("panel/users/<int:pk>/unblock/", views.admin_user_unblock, name="admin_user_unblock"),

    # Admin: categories CRUD (Module 13)
    path("panel/categories/", views.admin_categories, name="admin_categories"),
    path("panel/categories/<int:pk>/delete/", views.admin_category_delete, name="admin_category_delete"),
]
