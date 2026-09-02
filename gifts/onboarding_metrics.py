from django.db.models import F
from django.utils import timezone

from gifts.models import OnboardingDailyCount

EVENTS = {"registered", "verified", "profile_completed", "group_created", "group_joined", "group_skipped"}


def record_onboarding_event(event, user):
    """Daily totals only: no user identifier, address, nickname, token or request.

    `user` is passed so the demo account is excluded here, once, instead of at
    every call site."""
    if event not in EVENTS:
        raise ValueError("Unknown onboarding event")
    if getattr(user, "is_demo", False):
        return
    # get_or_create is race-safe (savepoint + re-get on the unique constraint);
    # the F() bump is atomic, so concurrent events on the same day still add up.
    counter, created = OnboardingDailyCount.objects.get_or_create(
        day=timezone.localdate(), event=event, defaults={"count": 1}
    )
    if not created:
        OnboardingDailyCount.objects.filter(pk=counter.pk).update(count=F("count") + 1)
