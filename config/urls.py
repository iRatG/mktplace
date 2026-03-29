from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

handler404 = "config.urls.page_not_found_view"
handler500 = "config.urls.server_error_view"


def page_not_found_view(request, exception=None):
    from django.template import loader
    from django.http import HttpResponseNotFound
    t = loader.get_template("404.html")
    return HttpResponseNotFound(t.render())


def server_error_view(request):
    from django.template import loader
    from django.http import HttpResponse
    t = loader.get_template("500.html")
    return HttpResponse(t.render(), status=500)


urlpatterns = [
    path('admin/', admin.site.urls),

    # Web frontend
    path('', include('apps.web.urls')),

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
