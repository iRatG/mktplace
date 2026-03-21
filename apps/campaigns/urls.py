from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import CampaignViewSet, ResponseViewSet

app_name = "campaigns"

router = DefaultRouter()
router.register(r"", CampaignViewSet, basename="campaign")
router.register(r"responses", ResponseViewSet, basename="response")

urlpatterns = [
    path("", include(router.urls)),
]
