from django.urls import path

from .views import (
    MarkAllReadView,
    MarkReadView,
    NotificationListView,
    NotificationSettingsView,
)

app_name = "notifications"

urlpatterns = [
    path("", NotificationListView.as_view(), name="notification-list"),
    path("mark-all-read/", MarkAllReadView.as_view(), name="mark-all-read"),
    path("<int:pk>/read/", MarkReadView.as_view(), name="mark-read"),
    path("settings/", NotificationSettingsView.as_view(), name="notification-settings"),
]
