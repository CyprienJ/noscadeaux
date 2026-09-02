from collections.abc import Callable
from dataclasses import dataclass

from django.urls import reverse
from django.utils import timezone

# Account state invariants (enforced by User.Meta constraints and this module):
#   - onboarding_completed_at IS NOT NULL  <=>  onboarding_version >= 1
#     (constraint `onboarding_completed_iff_versioned`)
#   - a new account starts at onboarding_version = 0 with both
#     onboarding_completed_at and profile_completed_at NULL
#   - profile_completed_at may be set while onboarding_version is still 0
#     (profile done, group step pending); it is never cleared afterwards
#   - is_verified only ever goes False -> True during onboarding
# get_onboarding_next_url relies on these holding, not on test ordering alone.
CURRENT_ONBOARDING_VERSION = 1
PENDING_GROUP_INVITE_SESSION_KEY = "pending_group_invite_token"
GROUP_INVITATION_PENDING_PREFIX = "invite:"


def onboarding_is_complete(user):
    return user.onboarding_version >= CURRENT_ONBOARDING_VERSION


def get_pending_group_invite(user, request=None):
    """Return a validated invitation identifier, never an arbitrary return URL."""
    if request is not None:
        token = request.session.get(PENDING_GROUP_INVITE_SESSION_KEY, "")
        if token:
            return token
    if getattr(user, "is_authenticated", False):
        return user.pending_group_invite_token
    return ""


def remember_pending_group_invite(request, token, user=None):
    token = (token or "").strip()
    if not token:
        return

    request.session[PENDING_GROUP_INVITE_SESSION_KEY] = token
    target_user = user or request.user
    if getattr(target_user, "is_authenticated", False) and target_user.pending_group_invite_token != token:
        target_user.pending_group_invite_token = token
        target_user.save(update_fields=["pending_group_invite_token"])


def clear_pending_group_invite(request, user=None):
    request.session.pop(PENDING_GROUP_INVITE_SESSION_KEY, None)
    target_user = user or request.user
    if getattr(target_user, "is_authenticated", False) and target_user.pending_group_invite_token:
        target_user.pending_group_invite_token = ""
        target_user.save(update_fields=["pending_group_invite_token"])


def complete_onboarding(user):
    update_fields = []
    if user.onboarding_version < CURRENT_ONBOARDING_VERSION:
        user.onboarding_version = CURRENT_ONBOARDING_VERSION
        update_fields.append("onboarding_version")
    if user.onboarding_completed_at is None:
        user.onboarding_completed_at = timezone.now()
        update_fields.append("onboarding_completed_at")
    if update_fields:
        user.save(update_fields=update_fields)


def group_invitation_pending_value(token):
    return f"{GROUP_INVITATION_PENDING_PREFIX}{token}"


# --- The onboarding flow as one ordered list -------------------------------
#
# The whole flow lives here. `get_onboarding_next_url` and AccountSetupMiddleware
# consume this list and hold no step logic of their own. Adding a step is adding
# one OnboardingStep entry: its name, how to tell it is done, the page it shows,
# and the route names that stay reachable while the user sits on it.

# Routes always reachable during setup, whatever the current step.
_COMMON_SETUP_URL_NAMES = frozenset(
    {
        "login",
        "logout",
        "welcome",
        "privacy",
        "bug_report",
        "bug_report_success",
        "account/password_reset",
        "account/password_reset_done",
        "account/password_reset_confirm",
        "account/password_reset_complete",
        "set_language",
        "verify_email_confirm",
    }
)


@dataclass(frozen=True)
class OnboardingStep:
    name: str
    url_name: str
    is_complete: Callable[[object], bool]
    allowed_url_names: frozenset


ONBOARDING_STEPS = (
    OnboardingStep(
        name="verify_email",
        url_name="verify_email_sent",
        is_complete=lambda user: user.is_verified,
        allowed_url_names=_COMMON_SETUP_URL_NAMES
        | {
            "account",
            "verify_email_sent",
            "resend_verification",
            "join_group",
            "group_invitation",
        },
    ),
    OnboardingStep(
        name="profile",
        url_name="onboarding_profile",
        is_complete=lambda user: user.profile_completed_at is not None,
        allowed_url_names=_COMMON_SETUP_URL_NAMES
        | {
            "onboarding_profile",
            "photo_upload_profile",
            "join_group",
            "group_invitation",
        },
    ),
    OnboardingStep(
        name="group",
        url_name="onboarding_group",
        is_complete=onboarding_is_complete,
        allowed_url_names=_COMMON_SETUP_URL_NAMES
        | {
            "onboarding_group",
            "onboarding_join_group",
            "onboarding_group_skip",
            "create_group",
            "join_group",
            "join_group_confirm",
            "dismiss_group_invite",
            "group_invitation",
            "group_invitation_accept",
            "group_invitation_dismiss",
        },
    ),
)


def next_onboarding_step(user):
    """Pure: the first step this user has not completed, or None when done.

    Depends only on the user. Pending-invitation handling is a separate layer in
    `get_onboarding_next_url`."""
    if not getattr(user, "is_authenticated", False):
        return None
    for step in ONBOARDING_STEPS:
        if not step.is_complete(user):
            return step
    return None


def onboarding_stage(user):
    """The name of the step this user is on: one of the ``OnboardingStep.name``
    values (``"verify_email"``, ``"profile"``, ``"group"``) or ``"done"``.

    For call sites that only need to branch on the stage instead of re-deriving
    it from ``is_verified`` / ``profile_completed_at`` by hand."""
    step = next_onboarding_step(user)
    return step.name if step is not None else "done"


def _pending_group_invite_url(pending_token):
    if pending_token.startswith(GROUP_INVITATION_PENDING_PREFIX):
        return reverse(
            "group_invitation",
            kwargs={"token": pending_token.removeprefix(GROUP_INVITATION_PENDING_PREFIX)},
        )
    return reverse("join_group", kwargs={"token": pending_token})


def get_onboarding_next_url(user, request=None):
    """Return the next safe, internal URL for the account setup flow.

    This is the contextual layer over `next_onboarding_step`: once the user is
    verified with a profile, a pending invitation wins over the generic group
    choice."""
    if not user.is_authenticated:
        return reverse("welcome")

    step = next_onboarding_step(user)
    if step is not None and step.name in ("verify_email", "profile"):
        return reverse(step.url_name)

    pending_token = get_pending_group_invite(user, request)
    if pending_token:
        return _pending_group_invite_url(pending_token)

    if step is not None:
        return reverse(step.url_name)
    return reverse("dashboard")
