from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import CategoryListView, PlatformViewSet

app_name = "platforms"

router = DefaultRouter()
router.register(r"", PlatformViewSet, basename="platform")

urlpatterns = [
    path("categories/", CategoryListView.as_view(), name="category-list"),
    path("", include(router.urls)),
]
