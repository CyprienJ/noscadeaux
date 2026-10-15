"""Public, language-independent resources for the web app and Android TWA."""

from django.conf import settings
from django.contrib.staticfiles import finders
from django.http import JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.views.decorators.http import require_GET


@require_GET
def manifest(request):
    icons = [
        {
            "src": static(f"gifts/pwa/icon-{size}.png"),
            "sizes": f"{size}x{size}",
            "type": "image/png",
            "purpose": "any",
        }
        for size in (192, 512)
        if finders.find(f"gifts/pwa/icon-{size}.png")
    ]
    if not icons:
        # Keep the manifest valid before the final Android artwork is supplied.
        icons = [{"src": static("gifts/favicon.ico"), "sizes": "16x16 32x32 48x48", "type": "image/x-icon"}]
    return JsonResponse(
        {
            "id": "/",
            "name": "nosCadeaux",
            "short_name": "nosCadeaux",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#faf7f2",
            "theme_color": "#e11d48",
            "icons": icons,
        },
        content_type="application/manifest+json",
    )


@require_GET
def assetlinks(request):
    statements = []
    if settings.TWA_PACKAGE_NAME and settings.TWA_SHA256_FINGERPRINTS:
        statements.append(
            {
                "relation": ["delegate_permission/common.handle_all_urls"],
                "target": {
                    "namespace": "android_app",
                    "package_name": settings.TWA_PACKAGE_NAME,
                    "sha256_cert_fingerprints": settings.TWA_SHA256_FINGERPRINTS,
                },
            }
        )
    response = JsonResponse(statements, safe=False)
    response["Cache-Control"] = "public, max-age=300"
    return response


@require_GET
def service_worker(request):
    response = render(request, "pwa/service-worker.js", content_type="text/javascript")
    response["Cache-Control"] = "no-cache"
    response["Service-Worker-Allowed"] = "/"
    return response


@require_GET
def offline(request):
    # This document is cached for every visitor: never include account/session data.
    return render(request, "pwa/offline.html")


@require_GET
def account_deletion(request):
    return render(request, "account/deletion.html", {"contact_email": "cyprien.jorant@pm.me"})
