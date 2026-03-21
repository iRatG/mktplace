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

    # Catalog (for bloggers)
    path("catalog/", views.campaign_list, name="catalog"),

    # Responses
    path("responses/<int:pk>/accept/", views.response_accept, name="response_accept"),
    path("responses/<int:pk>/reject/", views.response_reject, name="response_reject"),

    # Platforms
    path("platforms/add/", views.platform_add, name="platform_add"),

    # Deals
    path("deals/", views.deal_list, name="deal_list"),
    path("deals/<int:pk>/", views.deal_detail, name="deal_detail"),
    path("deals/<int:pk>/submit-publication/", views.deal_submit_publication, name="deal_submit_publication"),
    path("deals/<int:pk>/confirm/", views.deal_confirm, name="deal_confirm"),
    path("deals/<int:pk>/cancel/", views.deal_cancel, name="deal_cancel"),

    # Billing
    path("wallet/", views.wallet_view, name="wallet"),
]
