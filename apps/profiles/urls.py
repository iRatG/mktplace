from django.urls import path

from .views import AdvertiserProfileView, BloggerProfileView, PublicBloggerProfileView

app_name = "profiles"

urlpatterns = [
    path("advertiser/me/", AdvertiserProfileView.as_view(), name="advertiser-profile"),
    path("blogger/me/", BloggerProfileView.as_view(), name="blogger-profile"),
    path(
        "blogger/<int:user_id>/",
        PublicBloggerProfileView.as_view(),
        name="public-blogger-profile",
    ),
]
