import hashlib
import hmac
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from gifts.models import AuthThrottleEvent


class AuthRateLimitError(Exception):
    """Raised when an IP exceeds the register / resend allowance in the window."""


def _client_ip(request):
    # Nginx overwrites X-Real-IP; the start of X-Forwarded-For is client-controlled.
    return request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR", "") or "unknown"


def _ip_hash(request):
    digest = hmac.new(
        settings.SECRET_KEY.encode(),
        _client_ip(request).encode(),
        hashlib.sha256,
    )
    return digest.hexdigest()


def enforce_auth_rate_limit(request, action, limit):
    """Record the attempt and raise AuthRateLimitError past `limit` in the window."""
    window_start = timezone.now() - timedelta(seconds=settings.AUTH_RATE_WINDOW_SECONDS)
    ip_hash = _ip_hash(request)
    AuthThrottleEvent.objects.create(ip_hash=ip_hash, action=action)
    recent_count = AuthThrottleEvent.objects.filter(
        action=action,
        ip_hash=ip_hash,
        created_at__gte=window_start,
    ).count()
    if recent_count > limit:
        raise AuthRateLimitError
