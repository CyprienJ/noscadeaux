from django.db import migrations


def create_missing_profiles(apps, schema_editor):
    users = apps.get_model("gifts", "User")
    managed_members = apps.get_model("gifts", "ManagedMember")
    database = schema_editor.connection.alias
    colors = [
        "oklch(60% 0.14 100)",
        "oklch(60% 0.14 180)",
        "oklch(60% 0.14 230)",
        "oklch(60% 0.14 290)",
        "oklch(60% 0.14 340)",
    ]

    for user in users.objects.using(database).filter(is_managed=True, managed_member_profile__isnull=True).iterator():
        # A managed profile belongs to one group, even if the account was added to several.
        group = user.gift_groups.using(database).order_by("pk").first()
        if group is None:
            continue
        count = managed_members.objects.using(database).filter(group_id=group.pk).count()
        managed_members.objects.using(database).create(
            user_id=user.pk,
            group_id=group.pk,
            name=user.nickname,
            color=colors[count % len(colors)],
        )


class Migration(migrations.Migration):
    dependencies = [
        ("gifts", "0038_shared_gift_publications"),
    ]

    operations = [
        migrations.RunPython(create_missing_profiles, migrations.RunPython.noop),
    ]
