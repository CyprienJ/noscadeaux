from django.db.models.signals import post_delete, pre_delete
from django.dispatch import receiver

from gifts.models import Group, ManagedMember, User


@receiver(pre_delete, sender=User)
def _capture_member_groups(sender, instance, **kwargs):
    # The M2M rows are gone by the time post_delete fires, so remember the groups now.
    if instance.is_managed:
        instance._groups_to_prune = ()
        return
    instance._groups_to_prune = list(instance.gift_groups.values_list("pk", flat=True))


@receiver(post_delete, sender=User)
def _prune_emptied_groups(sender, instance, using, **kwargs):
    # A group outlives the account that created it as long as real members remain.
    # Once its last non-managed member is gone, the group and its managed people
    # (which nobody could reach any more) are removed together.
    for group_id in getattr(instance, "_groups_to_prune", ()):
        group = Group.objects.using(using).filter(pk=group_id).first()
        if group is not None:
            group.delete_if_abandoned()


@receiver(post_delete, sender=ManagedMember)
def delete_managed_user(sender, instance, using, origin, **kwargs):
    # User deletion already collects its profile and gifts. Avoid a second cascade.
    if isinstance(origin, User) and origin.pk == instance.user_id:
        return
    if getattr(origin, "model", None) is User and origin.filter(pk=instance.user_id).exists():
        return
    # Deleting a group or its managed profile also removes the technical identity.
    # A future claimed (active/non-managed) identity must survive profile removal.
    User.objects.using(using).filter(pk=instance.user_id, is_managed=True, is_active=False).delete()
