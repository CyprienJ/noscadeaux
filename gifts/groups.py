import os
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from gifts.demo import demo_scope_forbidden_response, has_same_demo_scope
from gifts.forms import GroupForm
from gifts.models import MANAGED_MEMBER_COLORS, EventList, Group, ManagedMember, SharedGiftPublication, User
from gifts.photo_presets import is_valid_photo_preset, list_photo_presets

NOT_A_MEMBER = "You are not a member of this group."


@login_required
@require_POST
def create_group(request):
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
    else:
        for error in form.errors.values():
            messages.error(request, error.as_text())

    return redirect("dashboard")


@login_required
@require_GET
def join_group(request, token=None):
    # Redirect to event detail if the token belongs to an event list
    if EventList.objects.filter(access_token=token).exists():
        return redirect("event_detail", token=token)

    group = Group.objects.filter(group_token=token).first()
    if not group:
        return render(request, "groups/group_not_found.html", status=404)

    if not has_same_demo_scope(request.user, group):
        return demo_scope_forbidden_response()

    if request.user in group.members.all():
        messages.info(request, _("You are already a member of the group '%s'.") % group.name)
        return redirect("dashboard")

    return render(request, "groups/group_preview.html", status=200, context={"group": group})


@login_required
@require_GET
def join_group_confirm(request, token):
    group = Group.objects.filter(group_token=token).first()

    if group:
        if not has_same_demo_scope(request.user, group):
            return demo_scope_forbidden_response()
        if request.user in group.members.all():
            messages.info(request, _("You are already a member of the group '%s'.") % group.name)
        else:
            group.members.add(request.user)
            messages.success(request, _("You have joined the group '%s'!") % group.name)
        return redirect("group_detail", group_id=group.id)
    else:
        messages.error(request, _("No group found with this code."))

    return redirect("dashboard")


@login_required
@require_GET
def group_detail(request, group_id):
    group = Group.objects.filter(id=group_id).first()

    if not group:
        return render(request, "groups/group_not_found.html", status=404)

    if request.user not in group.members.all():
        return HttpResponseForbidden(_(NOT_A_MEMBER))

    managed_members = group.managed_members.all().order_by("created_at")
    return render(request, "groups/group_detail.html", {"group": group, "managed_members": managed_members})


@login_required
@require_GET
def view_managed_list(request, group_id, member_id):
    """Redirect to the standard view_list for the managed user."""
    group = get_object_or_404(Group, id=group_id)
    if request.user not in group.members.all():
        return HttpResponseForbidden(_(NOT_A_MEMBER))
    member = get_object_or_404(ManagedMember, id=member_id, group=group)
    if member.user:
        return redirect(f"{reverse('view_list', args=[member.user.id])}?from_group={group_id}")
    return redirect("group_detail", group_id=group_id)


@login_required
@require_POST
def add_managed_member(request, group_id):
    group = get_object_or_404(Group, id=group_id)
    if request.user not in group.members.all():
        return HttpResponseForbidden(_(NOT_A_MEMBER))
    name = request.POST.get("name", "").strip()
    if not name:
        messages.error(request, _("Name is required."))
        return redirect("group_detail", group_id=group_id)

    email = f"managed_{uuid.uuid4().hex[:12]}@noscadeaux.internal"
    with transaction.atomic():
        managed_user = User.objects.create(
            email=email,
            username=email,
            nickname=name,
            is_managed=True,
            is_demo=request.user.is_demo,
            is_verified=True,
            is_active=False,
        )
        ManagedMember.objects.create(
            user=managed_user,
            group=group,
            name=name,
            color=MANAGED_MEMBER_COLORS[group.managed_members.count() % len(MANAGED_MEMBER_COLORS)],
        )
        group.members.add(managed_user)
    return redirect(f"{reverse('view_list', args=[managed_user.id])}?from_group={group_id}")


@login_required
@require_POST
def rename_managed_member(request, group_id, member_id):
    group = get_object_or_404(Group, id=group_id)
    if request.user not in group.members.all():
        return HttpResponseForbidden(_(NOT_A_MEMBER))
    member = get_object_or_404(ManagedMember, id=member_id, group=group)
    name = request.POST.get("name", "").strip()
    if name:
        member.name = name
        member.save(update_fields=["name"])
        if member.user:
            member.user.nickname = name
            member.user.save(update_fields=["nickname"])
    if member.user:
        return redirect(f"{reverse('view_list', args=[member.user.id])}?from_group={group_id}")
    return redirect("group_detail", group_id=group_id)


@login_required
@require_POST
def delete_managed_member(request, group_id, member_id):
    group = get_object_or_404(Group, id=group_id)
    if request.user not in group.members.all():
        return HttpResponseForbidden(_(NOT_A_MEMBER))
    member = get_object_or_404(ManagedMember, id=member_id, group=group)
    if member.user:
        member.user.delete()  # cascades: deletes ManagedMember + all owned gifts
    else:
        member.delete()
    return redirect("group_detail", group_id=group_id)


@login_required
@require_POST
def leave_group(request, group_id):
    group = get_object_or_404(Group, id=group_id)
    if request.user in group.members.all():
        SharedGiftPublication.objects.filter(group=group, published_by=request.user).delete()
        group.members.remove(request.user)
        messages.success(request, _("You have left the group '%s'.") % group.name)
    if group.members.count() == 0:
        group.delete()
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
@require_POST
def regenerate_group_token(request, group_id):
    group = get_object_or_404(
        Group,
        id=group_id,
    )
    if request.user not in group.members.all():
        return HttpResponseForbidden(_(NOT_A_MEMBER))
    group.group_token = ""  # Will be regenerated in save()
    group.save()
    messages.success(request, _("New invitation code generated !"))
    return redirect("group_detail", group_id=group.id)
