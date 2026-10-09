from django.urls import path

from . import views

app_name = "web"

urlpatterns = [
    # Landing & static
    path("", views.landing, name="landing"),
    path("faq/", views.faq, name="faq"),
    path("support/", views.support_view, name="support"),

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
    path("campaigns/<int:pk>/finish/", views.campaign_finish, name="campaign_finish"),
    path("campaigns/<int:pk>/edit/", views.campaign_edit, name="campaign_edit"),
    path("campaigns/<int:pk>/submit/", views.campaign_submit, name="campaign_submit"),
    path("campaigns/<int:pk>/pause/", views.campaign_pause, name="campaign_pause"),
    path("campaigns/<int:pk>/resume/", views.campaign_resume, name="campaign_resume"),
    path("campaigns/<int:pk>/increase-budget/", views.campaign_increase_budget, name="campaign_increase_budget"),
    path("campaigns/<int:pk>/respond/", views.campaign_respond, name="campaign_respond"),
    path("campaigns/<int:pk>/proposal/accept/", views.campaign_proposal_accept, name="campaign_proposal_accept"),
    path("campaigns/<int:pk>/proposal/decline/", views.campaign_proposal_decline, name="campaign_proposal_decline"),

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
    path("my/responses/", views.my_responses, name="my_responses"),

    # Platforms
    path("platforms/add/", views.platform_add, name="platform_add"),
    path("platforms/<int:pk>/edit/", views.platform_edit, name="platform_edit"),
    path("platforms/<int:pk>/delete/", views.platform_delete, name="platform_delete"),

    # Profiles
    path("profile/", views.profile_view, name="profile"),
    path("profile/edit/", views.profile_edit, name="profile_edit"),
    path("bloggers/<int:pk>/", views.blogger_public_profile, name="blogger_public_profile"),

    # Deals
    path("campaigns/<int:pk>/invite/", views.campaign_invite, name="campaign_invite"),
    path("deals/", views.deal_list, name="deal_list"),
    path("deals/<int:pk>/", views.deal_detail, name="deal_detail"),
    path("deals/<int:pk>/submit-publication/", views.deal_submit_publication, name="deal_submit_publication"),
    path("deals/<int:pk>/confirm/", views.deal_confirm, name="deal_confirm"),
    path("deals/<int:pk>/cancel/", views.deal_cancel, name="deal_cancel"),
    path("deals/<int:pk>/dispute/", views.deal_dispute, name="deal_dispute"),
    path("deals/<int:pk>/claim/materials/", views.deal_claim_materials, name="deal_claim_materials"),
    path("deals/<int:pk>/termination/propose/", views.deal_propose_termination, name="deal_propose_termination"),
    path("deals/<int:pk>/termination/accept/", views.deal_accept_termination, name="deal_accept_termination"),
    path("deals/<int:pk>/termination/decline/", views.deal_decline_termination, name="deal_decline_termination"),
    path("deals/<int:pk>/publication-date/propose/", views.deal_propose_publication_date,
         name="deal_propose_publication_date"),
    path("deals/<int:pk>/publication-date/accept/", views.deal_accept_publication_date,
         name="deal_accept_publication_date"),
    path("deals/<int:pk>/publication-date/decline/", views.deal_decline_publication_date,
         name="deal_decline_publication_date"),
    path("deals/<int:pk>/messages/", views.deal_send_message, name="deal_send_message"),  # Sprint 6
    path("deals/<int:pk>/submit-creative/", views.deal_submit_creative, name="deal_submit_creative"),  # Sprint 7
    path("deals/<int:pk>/approve-creative/", views.deal_approve_creative, name="deal_approve_creative"),  # Sprint 7
    path("deals/<int:pk>/reject-creative/", views.deal_reject_creative, name="deal_reject_creative"),  # Sprint 7

    # Billing
    path("wallet/", views.wallet_view, name="wallet"),
    path("profile/payout-requisites/", views.payout_requisites_view, name="payout_requisites"),
    path("panel/payout-requisites/", views.admin_payout_requisites, name="admin_payout_requisites"),
    path("panel/payout-requisites/<int:pk>/approve/", views.admin_payout_requisites_approve, name="admin_payout_requisites_approve"),
    path("panel/payout-requisites/<int:pk>/reject/", views.admin_payout_requisites_reject, name="admin_payout_requisites_reject"),

    # Admin panel (staff only)
    path("panel/", views.admin_dashboard, name="admin_dashboard"),
    path("panel/campaigns/", views.admin_campaigns, name="admin_campaigns"),
    path("panel/campaigns/<int:pk>/", views.admin_campaign_detail, name="admin_campaign_detail"),
    path("panel/campaigns/<int:pk>/approve/", views.admin_campaign_approve, name="admin_campaign_approve"),
    path("panel/campaigns/<int:pk>/reject/", views.admin_campaign_reject, name="admin_campaign_reject"),
    path("panel/campaigns/<int:pk>/propose/", views.admin_campaign_propose, name="admin_campaign_propose"),
    path("panel/platforms/", views.admin_platforms, name="admin_platforms"),
    path("panel/platforms/<int:pk>/approve/", views.admin_platform_approve, name="admin_platform_approve"),
    path("panel/platforms/<int:pk>/reject/", views.admin_platform_reject, name="admin_platform_reject"),
    path("panel/disputes/", views.admin_disputes, name="admin_disputes"),
    path("reviews/", views.platform_reviews, name="platform_reviews"),
    path("panel/reviews/", views.admin_platform_reviews, name="admin_platform_reviews"),
    path("panel/reviews/<int:pk>/moderate/", views.admin_platform_review_moderate, name="admin_platform_review_moderate"),
    path("panel/reviews/<int:pk>/remove/", views.admin_platform_review_remove, name="admin_platform_review_remove"),
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

    # Analytics (Module 12)
    path("analytics/", views.analytics_view, name="analytics"),

    # CPA Tracking (Sprint 8) — public endpoints, no login required
    path("t/<slug:slug>/", views.cpa_click_track, name="cpa_click_track"),
    path("pb/", views.cpa_postback, name="cpa_postback"),

    # Permit documents — user (REQ-2)
    path("profile/permits/", views.permit_list, name="permit_list"),
    path("profile/permits/upload/", views.permit_upload, name="permit_upload"),
    path("profile/permits/<int:pk>/delete/", views.permit_delete, name="permit_delete"),

    # Permit documents — admin (REQ-2)
    path("panel/permits/", views.admin_permits, name="admin_permits"),
    path("panel/permits/<int:pk>/approve/", views.admin_permit_approve, name="admin_permit_approve"),
    path("panel/permits/<int:pk>/reject/", views.admin_permit_reject, name="admin_permit_reject"),

    # Business queries — внутренние тикеты ИТ-команды + опросники для бизнеса без аккаунта
    path("tickets/", views.ticket_list, name="ticket_list"),
    path("tickets/new/", views.ticket_create, name="ticket_create"),
    path("tickets/<int:pk>/", views.ticket_detail, name="ticket_detail"),
    path("bq/<slug:token>/", views.business_query_view, name="business_query"),

    # Legal pages (REQ-6)
    path("legal/terms/", views.terms_view, name="terms"),
    path("legal/oferta/", views.oferta_view, name="oferta"),

    # Регистрация юрлиц — рекламодатель (точка входа с нуля, без email/пароля)
    path("register/legal-entity/", views.legal_entity_submit, name="legal_entity_submit"),
    path("panel/legal-entities/", views.admin_legal_entities, name="admin_legal_entities"),
    path("panel/legal-entities/all/", views.admin_legal_entity_registry, name="admin_legal_entity_registry"),
    path("panel/legal-entities/<int:pk>/", views.admin_legal_entity_detail, name="admin_legal_entity_detail"),
    path("panel/legal-entities/<int:pk>/approve/", views.admin_legal_entity_approve, name="admin_legal_entity_approve"),
    path("panel/legal-entities/<int:pk>/reject/", views.admin_legal_entity_reject, name="admin_legal_entity_reject"),
    path("panel/legal-entities/<int:pk>/ddocs/", views.admin_legal_entity_ddocs_update, name="admin_legal_entity_ddocs_update"),
    path("panel/legal-entities/<int:pk>/issue-access/", views.admin_legal_entity_issue_access, name="admin_legal_entity_issue_access"),

    # Регистрация блогеров — подтверждение личности (OneID) + статус ИП
    path("register/blogger/verify/", views.blogger_identity_submit, name="blogger_identity_submit"),
    path("profile/ip-application/", views.ip_application_upload, name="ip_application_upload"),
    path("profile/ip-application/list/", views.ip_application_list, name="ip_application_list"),
    path("panel/ip-applications/", views.admin_ip_applications, name="admin_ip_applications"),
    path("panel/ip-applications/<int:pk>/approve/", views.admin_ip_application_approve, name="admin_ip_application_approve"),
    path("panel/ip-applications/<int:pk>/reject/", views.admin_ip_application_reject, name="admin_ip_application_reject"),
]
