from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import CampaignViewSet, ResponseViewSet

app_name = "campaigns"

router = DefaultRouter()
# "responses" — до пустого префикса: иначе /responses/ совпадает с campaign-detail (pk="responses"),
# и список/создание откликов через API недоступны.
router.register(r"responses", ResponseViewSet, basename="response")
router.register(r"", CampaignViewSet, basename="campaign")

urlpatterns = [
    path("", include(router.urls)),
]
