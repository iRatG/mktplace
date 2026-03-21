from django.urls import path

from .views import AdminDashboardView, AdvertiserDashboardView, BloggerDashboardView

app_name = "analytics"

urlpatterns = [
    path("advertiser/", AdvertiserDashboardView.as_view(), name="advertiser-dashboard"),
    path("blogger/", BloggerDashboardView.as_view(), name="blogger-dashboard"),
    path("admin/", AdminDashboardView.as_view(), name="admin-dashboard"),
]
