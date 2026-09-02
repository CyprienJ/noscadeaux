from django.conf import settings
from django.shortcuts import redirect
from django.urls import Resolver404, resolve

from gifts.onboarding import get_onboarding_next_url, next_onboarding_step


class AccountSetupMiddleware:
    """Keeps a user on their current onboarding step's pages until it is done.

    All step knowledge lives in gifts.onboarding.ONBOARDING_STEPS; this class
    only resolves the current route once and compares it to the step's allowed
    route names."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            step = next_onboarding_step(request.user)
            if step is not None and not self._is_allowed(request, step):
                return redirect(get_onboarding_next_url(request.user, request))
        return self.get_response(request)

    @staticmethod
    def _is_allowed(request, step):
        if request.path.startswith((settings.STATIC_URL, settings.MEDIA_URL)):
            return True

        try:
            match = resolve(request.path)
        except Resolver404:
            return False

        if request.user.is_staff and match.namespace == "admin":
            return True

        url_name = match.url_name
        if url_name and url_name.startswith("event_"):
            return True
        return url_name in step.allowed_url_names
