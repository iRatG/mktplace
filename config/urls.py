from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path('admin/', admin.site.urls),

    # API docs
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),

    # API v1
    path('api/v1/auth/', include('apps.users.urls')),
    path('api/v1/profiles/', include('apps.profiles.urls')),
    path('api/v1/platforms/', include('apps.platforms.urls')),
    path('api/v1/campaigns/', include('apps.campaigns.urls')),
    path('api/v1/deals/', include('apps.deals.urls')),
    path('api/v1/billing/', include('apps.billing.urls')),
    path('api/v1/notifications/', include('apps.notifications.urls')),
    path('api/v1/analytics/', include('apps.analytics.urls')),
]

if settings.DEBUG:
    import debug_toolbar
    urlpatterns = [path('__debug__/', include(debug_toolbar.urls))] + urlpatterns
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
