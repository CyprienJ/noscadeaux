import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from gifts.group_permissions import can_manage_group_people
from gifts.models import Group, ManagedMember, User

# Source of truth for running code. Migration 0044 keeps its own frozen copy on
# purpose: a data migration must not import application code that may change.
MANAGED_MEMBER_COLORS = (
    "oklch(62% 0.14 350)",
    "oklch(60% 0.14 200)",
    "oklch(60% 0.14 280)",
    "oklch(60% 0.14 150)",
    "oklch(60% 0.14 60)",
    "oklch(60% 0.14 240)",
    "oklch(60% 0.14 320)",
    "oklch(60% 0.14 100)",
)


def validate_managed_name(name):
    name = name.strip()
    if not name or len(name) > 100:
        raise ValidationError(_("Enter a name between 1 and 100 characters."))
    return name


@transaction.atomic
def create_managed_member(group, actor, name):
    group = Group.objects.select_for_update().get(pk=group.pk)
    if not can_manage_group_people(actor, group):
        raise PermissionDenied
    name = validate_managed_name(name)
    email = f"managed_{uuid.uuid4().hex}@noscadeaux.internal"
    user = User(
        email=email,
        username=email,
        nickname=name,
        is_managed=True,
        is_active=False,
        is_verified=True,
        is_demo=group.is_demo,
    )
    user.set_unusable_password()
    user.save()
    # Deterministic from the person's rank in this group, not a global counter.
    rank = group.managed_members.count()
    member = ManagedMember.objects.create(
        user=user,
        group=group,
        name=name,
        color=MANAGED_MEMBER_COLORS[rank % len(MANAGED_MEMBER_COLORS)],
    )
    group.members.add(user)
    return member


@transaction.atomic
def rename_managed_person(member, actor, name):
    member = ManagedMember.objects.select_for_update().select_related("group", "user").get(pk=member.pk)
    if not can_manage_group_people(actor, member.group):
        raise PermissionDenied
    name = validate_managed_name(name)
    member.name = name
    member.save(update_fields=["name"])
    if member.user_id:
        member.user.nickname = name
        member.user.save(update_fields=["nickname"])


@transaction.atomic
def delete_managed_person(member, actor):
    if not can_manage_group_people(actor, member.group):
        raise PermissionDenied
    member.delete()
