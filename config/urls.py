from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from gifts import pwa

urlpatterns = [
    path("manifest.webmanifest", pwa.manifest, name="pwa_manifest"),
    path("service-worker.js", pwa.service_worker, name="pwa_service_worker"),
    path("offline/", pwa.offline, name="pwa_offline"),
    path(".well-known/assetlinks.json", pwa.assetlinks, name="twa_assetlinks"),
    path("i18n/", include("django.conf.urls.i18n")),
    path("", include("gifts.extension_urls")),
]

urlpatterns += i18n_patterns(
    path("admin/", admin.site.urls),
    path("", include("gifts.urls")),
)

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
