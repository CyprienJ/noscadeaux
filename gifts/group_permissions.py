from functools import wraps

from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from gifts.models import Group

# Wrapped at declaration so makemessages extracts it; passed straight to
# HttpResponseForbidden, not through _().
NOT_A_MEMBER = gettext_lazy("You are not a member of this group.")


def can_manage_group_invitations(user, group):
    """Single policy point ready for future owner/admin/member roles."""
    return bool(user.is_authenticated and group.members.filter(pk=user.pk).exists())


def can_manage_group_people(user, group):
    return bool(
        user.is_authenticated
        and user.is_active
        and not user.is_managed
        and user.is_demo == group.is_demo
        and group.members.filter(pk=user.pk).exists()
    )


def can_claim_managed_identity(user, group):
    """May ``user`` join ``group`` by taking over one of its managed members?
    Same policy as ``can_manage_group_people`` minus the membership requirement,
    since the claimer is joining, not already inside."""
    return bool(user.is_authenticated and user.is_active and not user.is_managed and user.is_demo == group.is_demo)


def group_invitation_forbidden_response():
    return HttpResponseForbidden(_("You do not have permission to manage invitations for this group."))


def group_member_required(view):
    """Resolve the ``group_id`` URL argument to a Group, 403 for non-members, and
    hand the view a ``group`` instead of a ``group_id``. Pairs with
    ``@login_required`` (apply that outermost)."""

    @wraps(view)
    def wrapper(request, *args, group_id, **kwargs):
        group = get_object_or_404(Group, id=group_id)
        if not group.members.filter(pk=request.user.pk).exists():
            return HttpResponseForbidden(NOT_A_MEMBER)
        return view(request, group, *args, **kwargs)

    return wrapper
