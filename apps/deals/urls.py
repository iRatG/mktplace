from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ChatMessageViewSet, DealViewSet

app_name = "deals"

router = DefaultRouter()
router.register(r"", DealViewSet, basename="deal")

urlpatterns = [
    path("", include(router.urls)),
    path(
        "<int:deal_id>/messages/",
        ChatMessageViewSet.as_view({"get": "list", "post": "create"}),
        name="deal-messages",
    ),
]
