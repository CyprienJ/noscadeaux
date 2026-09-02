import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from gifts.demo import has_same_demo_scope
from gifts.forms import GroupForm, GroupInvitationEmailForm, OnboardingJoinGroupForm
from gifts.group_invitation_flow import (
    complete_group_step,
    dismiss_group_invitation,
    find_group_by_link_token,
    find_group_by_short_code,
    group_invitation_preview,
    invalid_group_invitation,
    join_previewed_group,
)
from gifts.group_invitations import InvitationRateLimitError, build_group_invitation_url, send_group_invitations
from gifts.group_permissions import (
    NOT_A_MEMBER,
    can_manage_group_invitations,
    group_invitation_forbidden_response,
    group_member_required,
)
from gifts.managed_members import create_managed_member, delete_managed_person, rename_managed_person
from gifts.models import EventList, Group, ManagedMember, SharedGiftPublication, generate_group_invitation_token
from gifts.onboarding import (
    get_onboarding_next_url,
    group_invitation_pending_value,
    onboarding_is_complete,
    onboarding_stage,
    remember_pending_group_invite,
)
from gifts.photo_presets import is_valid_photo_preset, list_photo_presets


@login_required
@require_GET
def onboarding_group(request):
    if onboarding_stage(request.user) != "group":
        return redirect(get_onboarding_next_url(request.user, request))
    return render(
        request,
        "registration/onboarding_group.html",
        {"group_form": GroupForm(), "join_form": OnboardingJoinGroupForm()},
    )


@login_required
@require_POST
def onboarding_join_group(request):
    if onboarding_is_complete(request.user):
        return redirect("dashboard")

    form = OnboardingJoinGroupForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Please enter a valid group code."))
        return redirect("onboarding_group")

    token = form.cleaned_data["code"]
    group = find_group_by_short_code(token)
    if not group or not has_same_demo_scope(request.user, group):
        messages.error(request, _("No group found with this code."))
        return redirect("onboarding_group")

    remember_pending_group_invite(request, group.group_token)
    return redirect("join_group", token=group.group_token)


@login_required
@require_POST
def onboarding_group_skip(request):
    complete_group_step(request, request.user, "group_skipped")
    messages.success(request, _("You can create or join a group whenever you like."))
    return redirect("dashboard")


@login_required
@require_POST
def create_group(request):
    is_onboarding = not onboarding_is_complete(request.user)
    form = GroupForm(request.POST)
    if form.is_valid():
        group = form.save(commit=False)
        group.created_by = request.user
        group.is_demo = request.user.is_demo
        group.save()
        group.members.add(request.user)
        msg = _("Group '%(name)s' created ! Share this code: %(token)s") % {
            "name": group.name,
            "token": group.group_token,
        }
        messages.success(request, msg)
        if is_onboarding:
            complete_group_step(request, request.user, "group_created")
        return redirect("group_invitations", group_id=group.id)
    else:
        for error in form.errors.values():
            messages.error(request, error.as_text())

        if is_onboarding:
            return redirect("onboarding_group")

    return redirect("dashboard")


@require_GET
def join_group(request, token=None):
    token = (token or "").strip()
    # Preserve the historical event-token shortcut.
    if EventList.objects.filter(access_token=token).exists():
        return redirect("event_detail", token=token)

    group = find_group_by_short_code(token)
    if not group:
        return invalid_group_invitation(request, token)
    code = group.group_token
    return group_invitation_preview(
        request,
        group,
        code,
        reverse("join_group_confirm", kwargs={"token": code}),
        reverse("dismiss_group_invite", kwargs={"token": code}),
    )


@require_GET
def group_invitation(request, token):
    pending_value = group_invitation_pending_value(token)
    group = find_group_by_link_token(token)
    if not group:
        return invalid_group_invitation(request, pending_value)
    return group_invitation_preview(
        request,
        group,
        pending_value,
        reverse("group_invitation_accept", kwargs={"token": token}),
        reverse("group_invitation_dismiss", kwargs={"token": token}),
    )


@login_required
@require_POST
def join_group_confirm(request, token):
    group = find_group_by_short_code(token)
    if not group:
        messages.error(request, _("No group found with this code."))
        return invalid_group_invitation(request, (token or "").strip())
    return join_previewed_group(request, group, group.group_token)


@login_required
@require_POST
def accept_group_invitation(request, token):
    pending_value = group_invitation_pending_value(token)
    group = find_group_by_link_token(token)
    if not group:
        return invalid_group_invitation(request, pending_value)
    return join_previewed_group(request, group, pending_value)


@require_POST
def dismiss_group_invite(request, token):
    return dismiss_group_invitation(request, (token or "").strip())


@require_POST
def dismiss_secure_group_invitation(request, token):
    return dismiss_group_invitation(request, group_invitation_pending_value(token))


def _render_group_invitations(request, group, form=None, status=200):
    return render(
        request,
        "groups/group_invitations.html",
        {
            "group": group,
            "invitation_url": build_group_invitation_url(group, request),
            "form": form or GroupInvitationEmailForm(),
            "email_invites_enabled": not group.is_demo,
            "managed_members": group.managed_members.select_related("user").order_by("created_at", "pk"),
        },
        status=status,
    )


@login_required
@require_GET
def group_invitations(request, group_id):
    group = get_object_or_404(Group, id=group_id)
    if not can_manage_group_invitations(request.user, group):
        return group_invitation_forbidden_response()
    return _render_group_invitations(request, group)


@login_required
@require_POST
def send_group_invitation_emails(request, group_id):
    group = get_object_or_404(Group, id=group_id)
    if not can_manage_group_invitations(request.user, group):
        return group_invitation_forbidden_response()
    if group.is_demo:
        return HttpResponseForbidden(_("The public demo cannot send invitation emails."))

    form = GroupInvitationEmailForm(request.POST)
    if not form.is_valid():
        return _render_group_invitations(request, group, form=form, status=400)

    try:
        result = send_group_invitations(group, request.user, form.cleaned_data["emails"], request)
    except InvitationRateLimitError:
        form.add_error(None, _("Too many invitations were sent recently. Please try again later."))
        return _render_group_invitations(request, group, form=form, status=429)

    if result.failed_count:
        messages.warning(
            request,
            _("Some invitations could not be sent. You can try again without recreating the group."),
        )
    else:
        messages.success(request, _("Invitations sent."))
    return redirect("group_invitations", group_id=group.id)


@login_required
@require_POST
def regenerate_group_invitation_token(request, group_id):
    group = get_object_or_404(Group, id=group_id)
    if not can_manage_group_invitations(request.user, group):
        return group_invitation_forbidden_response()
    group.invitation_token = generate_group_invitation_token()
    group.save(update_fields=["invitation_token"])
    messages.success(request, _("A new invitation link has been generated. The previous link no longer works."))
    return redirect("group_invitations", group_id=group.id)


@login_required
@require_GET
def group_detail(request, group_id):
    group = Group.objects.filter(id=group_id).first()

    if not group:
        return render(request, "groups/group_not_found.html", status=404)

    if request.user not in group.members.all():
        return HttpResponseForbidden(NOT_A_MEMBER)

    managed_members = group.managed_members.all().order_by("created_at")
    return render(request, "groups/group_detail.html", {"group": group, "managed_members": managed_members})


def _managed_member_redirect(request, group, member):
    """Where the managed-people actions return to: the invitations page when they
    were triggered from there, otherwise the person's list (or the group)."""
    if request.POST.get("return_to") == "invitations":
        return redirect("group_invitations", group_id=group.id)
    if member is not None and member.user_id:
        return redirect(f"{reverse('view_list', args=[member.user_id])}?from_group={group.id}")
    return redirect("group_detail", group_id=group.id)


@login_required
@group_member_required
@require_GET
def view_managed_list(request, group, member_id):
    """Redirect to the standard view_list for the managed user."""
    member = get_object_or_404(ManagedMember, id=member_id, group=group)
    return _managed_member_redirect(request, group, member)


@login_required
@group_member_required
@require_POST
def add_managed_member(request, group):
    try:
        member = create_managed_member(group, request.user, request.POST.get("name", ""))
    except ValidationError as error:
        messages.error(request, error.messages[0])
        return _managed_member_redirect(request, group, member=None)
    messages.success(request, _("Person added. You can add another person or open their list."))
    return _managed_member_redirect(request, group, member)


@login_required
@group_member_required
@require_POST
def rename_managed_member(request, group, member_id):
    member = get_object_or_404(ManagedMember, id=member_id, group=group)
    try:
        rename_managed_person(member, request.user, request.POST.get("name", ""))
    except ValidationError as error:
        messages.error(request, error.messages[0])
    return _managed_member_redirect(request, group, member)


@login_required
@group_member_required
@require_POST
def delete_managed_member(request, group, member_id):
    member = get_object_or_404(ManagedMember, id=member_id, group=group)
    delete_managed_person(member, request.user)
    return _managed_member_redirect(request, group, member=None)


@login_required
@require_POST
def leave_group(request, group_id):
    group = get_object_or_404(Group, id=group_id)
    if request.user in group.members.all():
        SharedGiftPublication.objects.filter(group=group, published_by=request.user).delete()
        group.members.remove(request.user)
        messages.success(request, _("You have left the group '%s'.") % group.name)
    group.delete_if_abandoned()
    return redirect("dashboard")


@login_required
@require_POST
def edit_group(request, group_id):
    group = get_object_or_404(Group, id=group_id)

    if request.user not in group.members.all():
        return redirect("dashboard")

    if request.method == "POST":
        new_name = request.POST.get("name")
        new_description = request.POST.get("description")
        new_image = request.FILES.get("image")

        if new_name:
            group.name = new_name

        if new_image:
            if group.image and os.path.isfile(group.image.path):
                os.remove(group.image.path)
            group.image = new_image
            group.image_preset = ""

        group.description = new_description

        new_show_history = request.POST.get("show_history") == "on"
        if group.show_history and not new_show_history:
            from .models import Gift

            Gift.objects.filter(group_reserved_on=group, offered=True).delete()
        group.show_history = new_show_history
        group.show_balance = request.POST.get("show_balance") == "on"

        group.save()

        return redirect("group_detail", group_id=group.id)

    return redirect("group_detail", group_id=group.id)


@login_required
def group_photo_upload(request, group_id):
    group = get_object_or_404(Group, pk=group_id, members=request.user)
    if request.method == "POST":
        preset = request.POST.get("preset", "")
        if preset:
            if not is_valid_photo_preset("group", preset):
                return JsonResponse({"success": False, "error": _("Invalid preset photo.")}, status=400)
            old = group.image
            if old and os.path.isfile(old.path):
                os.remove(old.path)
            group.image = None
            group.image_preset = preset
            group.save(update_fields=["image", "image_preset"])
            return JsonResponse({"success": True, "url": group.display_image_url})

        uploaded = request.FILES.get("photo")
        if not uploaded:
            return JsonResponse({"success": False, "error": "No file"}, status=400)
        old = group.image
        if old and os.path.isfile(old.path):
            os.remove(old.path)
        group.image = uploaded
        group.image_preset = ""
        group.save(update_fields=["image", "image_preset"])
        return JsonResponse({"success": True})
    return render(
        request,
        "photos/photo_upload.html",
        {
            "context_type": "group",
            "group": group,
            "photo_presets": list_photo_presets("group"),
            "back_url": reverse("group_detail", args=[group_id]),
        },
    )


@login_required
@group_member_required
@require_POST
def regenerate_group_token(request, group):
    group.group_token = ""  # Will be regenerated in save()
    group.save()
    messages.success(request, _("New invitation code generated !"))
    return redirect("group_detail", group_id=group.id)
