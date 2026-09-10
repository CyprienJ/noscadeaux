import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from gifts.group_permissions import can_claim_managed_identity, can_manage_group_people
from gifts.models import Gift, Group, ManagedMember, User

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


class ManagedIdentityUnavailableError(Exception):
    """The managed member can no longer be claimed: it was deleted meanwhile
    (a racing claimer won) or it is a legacy row with no usable technical user."""


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


@transaction.atomic
def claim_managed_identity(user, member):
    """``user`` (a real, active, non-managed visitor) takes over ``member``'s
    technical identity for ``member.group``:

      1. ``user`` joins the group.
      2. Every gift owned by the technical user is moved to ``user``. Gifts the
         technical user also created become ``user``'s own wishes; gifts created
         by other real members keep their ``created_by`` so ``created_by != owner``
         and they surface as ``user``'s surprises for this group. Each moved gift
         is pinned to this group only, so surprises don't leak elsewhere.
      3. The ManagedMember is deleted; the post_delete signal removes the
         technical User (and its ``group.members`` row).

    The row is locked for the transaction so two racing claimers can't both win.

    Raises:
      PermissionDenied           -- ``user`` may not join this group.
      ManagedIdentityUnavailableError -- member already claimed / legacy row.
    """
    try:
        member = ManagedMember.objects.select_for_update().select_related("group", "user").get(pk=member.pk)
    except ManagedMember.DoesNotExist as exc:
        raise ManagedIdentityUnavailableError from exc

    group = member.group
    if not can_claim_managed_identity(user, group):
        raise PermissionDenied

    managed_user = member.user
    if managed_user is None or managed_user.is_active or not managed_user.is_managed:
        # Legacy / corrupted profile: nothing safe to take over.
        raise ManagedIdentityUnavailableError

    group.members.add(user)  # idempotent

    for gift in Gift.objects.filter(owner=managed_user):
        fields = ["owner"]
        gift.owner = user
        if gift.created_by_id == managed_user.id:
            gift.created_by = user
            fields.append("created_by")
        if gift.managed_member_id == member.id:  # defensive: legacy FK
            gift.managed_member = None
            fields.append("managed_member")
        gift.save(update_fields=fields)
        gift.visible_in.set([group])

    member.delete()  # post_delete -> delete_managed_user removes the technical User
    return group
