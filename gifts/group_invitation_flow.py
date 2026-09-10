"""The join-by-link / join-by-code funnel: previewing a group, joining it, and
dismissing the invitation.

`gifts.groups` keeps only the thin URL-facing views; every decision about what a
visitor may see and where they go next lives here so the short code and the
private link share exactly one implementation.
"""

from enum import Enum

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from gifts.demo import demo_scope_forbidden_response, has_same_demo_scope
from gifts.managed_members import ManagedIdentityUnavailableError, claim_managed_identity
from gifts.models import Group
from gifts.onboarding import (
    clear_pending_group_invite,
    complete_onboarding,
    get_onboarding_next_url,
    get_pending_group_invite,
    onboarding_is_complete,
    onboarding_stage,
    remember_pending_group_invite,
)
from gifts.onboarding_metrics import record_onboarding_event


class InvitePreviewState(Enum):
    """What a visitor is allowed to do with an invitation right now."""

    DEMO_OUT_OF_SCOPE = "demo_out_of_scope"
    ANONYMOUS = "anonymous"
    ONBOARDING_INCOMPLETE = "onboarding_incomplete"
    ALREADY_MEMBER = "already_member"
    CAN_JOIN = "can_join"


def complete_group_step(request, user, event):
    """Finish onboarding at the group step exactly once: stamp completion, record
    the anonymous daily metric, and drop any now-moot pending invitation.

    Used by "create a group" and "skip"; joining a previewed group has its own
    slightly different pending-token handling (see `join_previewed_group`)."""
    newly_completed = not onboarding_is_complete(user)
    complete_onboarding(user)
    if newly_completed:
        record_onboarding_event(event, user)
    clear_pending_group_invite(request, user)


def invalid_group_invitation(request, pending_value):
    """Response for a token that matches no group: clear it if the visitor was
    carrying it, and send an onboarding user back to the group choice."""
    if get_pending_group_invite(request.user, request) == pending_value:
        clear_pending_group_invite(request)
        if request.user.is_authenticated and not onboarding_is_complete(request.user):
            messages.error(request, _("This invitation is no longer valid. Choose another group."))
            return redirect("onboarding_group")
    return render(request, "groups/group_not_found.html", status=404)


def _preview_state(request, group):
    if request.user.is_authenticated:
        if not has_same_demo_scope(request.user, group):
            return InvitePreviewState.DEMO_OUT_OF_SCOPE
        if onboarding_stage(request.user) in ("verify_email", "profile"):
            return InvitePreviewState.ONBOARDING_INCOMPLETE
        if request.user in group.members.all():
            return InvitePreviewState.ALREADY_MEMBER
        return InvitePreviewState.CAN_JOIN
    if group.is_demo:
        return InvitePreviewState.DEMO_OUT_OF_SCOPE
    return InvitePreviewState.ANONYMOUS


def group_invitation_preview(request, group, pending_value, accept_url, dismiss_url):
    """The limited group card shown for both the short code and the private link.
    `pending_value` is what gets remembered as the pending invitation;
    `accept_url` / `dismiss_url` are the token-specific follow-up routes."""
    state = _preview_state(request, group)
    if state is InvitePreviewState.DEMO_OUT_OF_SCOPE:
        return demo_scope_forbidden_response()

    remember_pending_group_invite(request, pending_value)

    if state is InvitePreviewState.ONBOARDING_INCOMPLETE:
        return redirect(get_onboarding_next_url(request.user, request))

    if state is InvitePreviewState.ALREADY_MEMBER:
        messages.info(request, _("You are already a member of the group '%s'.") % group.name)
        if onboarding_is_complete(request.user):
            clear_pending_group_invite(request)
            return redirect("group_detail", group_id=group.id)

    return render(
        request,
        "groups/group_preview.html",
        {
            "group": group,
            # The private link and the short code both reveal only name, image
            # and member count; the member list is for people already inside.
            "show_members": state is InvitePreviewState.ALREADY_MEMBER,
            "already_member": state is InvitePreviewState.ALREADY_MEMBER,
            "onboarding_incomplete": request.user.is_authenticated and not onboarding_is_complete(request.user),
            "accept_url": accept_url,
            "dismiss_url": dismiss_url,
            # Only a visitor who can actually join is offered "I am <managed member>".
            "claimable_managed_members": (
                list(group.managed_members.order_by("created_at", "pk")) if state is InvitePreviewState.CAN_JOIN else []
            ),
        },
    )


def join_previewed_group(request, group, pending_value):
    """POST target behind the preview's "join" button, for a logged-in user."""
    if not has_same_demo_scope(request.user, group):
        return demo_scope_forbidden_response()

    claim_id = (request.POST.get("claim_managed_member") or "").strip()
    if claim_id.isdigit():
        return _join_as_managed_member(request, group, pending_value, int(claim_id))

    if request.user in group.members.all():
        messages.info(request, _("You are already a member of the group '%s'.") % group.name)
    else:
        group.members.add(request.user)
        messages.success(request, _("You have joined the group '%s'!") % group.name)
    return _complete_join(request, group, pending_value)


def _complete_join(request, group, pending_value):
    if not onboarding_is_complete(request.user):
        complete_onboarding(request.user)
        record_onboarding_event("group_joined", request.user)
    # Only drop the invitation that was just accepted; a pending invite to some
    # other group is left in place on purpose.
    if get_pending_group_invite(request.user, request) == pending_value:
        clear_pending_group_invite(request)
    return redirect("group_detail", group_id=group.id)


def _join_as_managed_member(request, group, pending_value, claim_id):
    """The visitor picked "I am <managed member>" on the preview page."""
    member = group.managed_members.filter(pk=claim_id).first()
    if member is not None:
        try:
            claim_managed_identity(request.user, member)
        except PermissionDenied:
            return demo_scope_forbidden_response()
        except ManagedIdentityUnavailableError:
            member = None
        else:
            messages.success(
                request,
                _("You joined the group '%(group)s' as %(person)s — their wishes are now yours.")
                % {"group": group.name, "person": member.name},
            )
            return _complete_join(request, group, pending_value)

    # The member vanished, a claimer won the race, or the id was tampered with.
    group.members.add(request.user)
    messages.info(
        request,
        _("That choice is no longer available, so you simply joined the group '%s'.") % group.name,
    )
    return _complete_join(request, group, pending_value)


def dismiss_group_invitation(request, pending_value):
    if get_pending_group_invite(request.user, request) == pending_value:
        clear_pending_group_invite(request)
    if not request.user.is_authenticated:
        return redirect("welcome")
    if not onboarding_is_complete(request.user):
        return redirect("onboarding_group")
    return redirect("dashboard")


def find_group_by_short_code(token):
    return Group.objects.filter(group_token__iexact=(token or "").strip()).first()


def find_group_by_link_token(token):
    return Group.objects.filter(invitation_token=token).first()
