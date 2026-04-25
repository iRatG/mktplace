from .auth import (
    login_view,
    register_view,
    logout_view,
    email_confirm_view,
    password_reset_request_view,
    password_reset_confirm_view,
)
from .pages import (
    landing,
    faq,
    terms_view,
    oferta_view,
    _redirect_dashboard,
    advertiser_dashboard,
    blogger_dashboard,
)
from .campaigns import (
    campaign_list,
    campaign_detail,
    campaign_create,
    campaign_edit,
    campaign_submit,
    campaign_pause,
    campaign_resume,
    campaign_respond,
    response_accept,
    response_reject,
)
from .deals import (
    deal_list,
    deal_detail,
    deal_submit_publication,
    deal_confirm,
    deal_cancel,
    deal_send_message,
    deal_submit_creative,
    deal_approve_creative,
    deal_reject_creative,
    deal_review_submit,
)
from .platforms import (
    platform_add,
    platform_edit,
    platform_delete,
)
from .profiles import (
    profile_view,
    profile_edit,
    blogger_public_profile,
)
from .billing import (
    wallet_view,
)
from .catalog import (
    blogger_catalog,
    direct_offer_create,
    direct_offer_accept,
    direct_offer_reject,
)
from .admin_panel import (
    _staff_required,
    admin_dashboard,
    admin_campaigns,
    admin_campaign_approve,
    admin_campaign_reject,
    admin_platforms,
    admin_platform_approve,
    admin_platform_reject,
    admin_disputes,
    admin_dispute_resolve,
    admin_withdrawals,
    admin_users,
    admin_withdrawal_approve,
    admin_withdrawal_reject,
    admin_user_block,
    admin_user_unblock,
    admin_categories,
    admin_category_delete,
)
from .notifications import (
    notification_list,
    notification_mark_all_read,
)
from .analytics import (
    analytics_view,
    _analytics_advertiser,
    _analytics_blogger,
)
from .cpa import (
    cpa_click_track,
    cpa_postback,
)
from .permits import (
    permit_list,
    permit_upload,
    permit_delete,
    admin_permits,
    admin_permit_approve,
    admin_permit_reject,
)
