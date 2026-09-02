import uuid

from django.db import migrations

# Frozen copy of gifts.managed_members.MANAGED_MEMBER_COLORS. Duplicated on
# purpose: a data migration must keep working even if the application palette
# changes later, so it must not import that module.
COLORS = (
    "oklch(62% 0.14 350)", "oklch(60% 0.14 200)", "oklch(60% 0.14 280)", "oklch(60% 0.14 150)",
    "oklch(60% 0.14 60)", "oklch(60% 0.14 240)", "oklch(60% 0.14 320)", "oklch(60% 0.14 100)",
)


def repair_managed_members(apps, schema_editor):
    db = schema_editor.connection.alias
    user_model = apps.get_model("gifts", "User")
    member_model = apps.get_model("gifts", "ManagedMember")
    gift_model = apps.get_model("gifts", "Gift")
    orphans = list(user_model.objects.using(db).filter(is_managed=True, managed_member_profile__isnull=True))
    for user in orphans:
        if user.is_active or user.gift_groups.count() != 1:
            raise RuntimeError(
                "Managed identities with an active account or zero/multiple groups need manual review before migration."
            )
    # Managed identities created by the old view (or given a profile by 0039) keep an
    # empty password; give every managed identity an unambiguously unusable one.
    user_model.objects.using(db).filter(is_managed=True).exclude(password__startswith="!").update(
        password="!", is_verified=True
    )
    for user in orphans:
        group = user.gift_groups.get()
        member_model.objects.using(db).create(
            user_id=user.pk, group_id=group.pk, name=user.nickname[:100],
            color=COLORS[(user.pk - 1) % len(COLORS)],
        )
        user_model.objects.using(db).filter(pk=user.pk).update(password="!", is_verified=True)

    for member in member_model.objects.using(db).select_related("group").iterator():
        if member.user_id is None:
            email = f"managed_{uuid.uuid4().hex}@noscadeaux.internal"
            user = user_model.objects.using(db).create(
                email=email, username=email, nickname=member.name, password="!",
                is_managed=True, is_active=False, is_verified=True, is_demo=member.group.is_demo,
            )
            member.user_id = user.pk
            gift_model.objects.using(db).filter(managed_member_id=member.pk).update(owner_id=user.pk)
        member.group.members.add(member.user_id)
        if not member.color:
            member.color = COLORS[(member.user_id - 1) % len(COLORS)]
        member.save(using=db, update_fields=["user", "color"])


class Migration(migrations.Migration):
    dependencies = [("gifts", "0043_onboarding_daily_counts")]
    operations = [migrations.RunPython(repair_managed_members, migrations.RunPython.noop)]
