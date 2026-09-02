import secrets

import django.db.models.deletion
import gifts.models
from django.conf import settings
from django.db import migrations, models


def populate_invitation_tokens(apps, schema_editor):
    group_model = apps.get_model("gifts", "Group")
    for group in group_model.objects.filter(invitation_token__isnull=True).iterator():
        group.invitation_token = secrets.token_urlsafe(32)
        group.save(update_fields=["invitation_token"])


class Migration(migrations.Migration):
    dependencies = [
        ("gifts", "0041_user_pending_group_invite_token"),
    ]

    operations = [
        migrations.AddField(
            model_name="group",
            name="invitation_token",
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
        migrations.RunPython(populate_invitation_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="group",
            name="invitation_token",
            field=models.CharField(default=gifts.models.generate_group_invitation_token, max_length=64, unique=True),
        ),
        migrations.CreateModel(
            name="GroupInvitationDispatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("requested_count", models.PositiveSmallIntegerField()),
                ("sent_count", models.PositiveSmallIntegerField(default=0)),
                ("failed_count", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "group",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="invitation_dispatches",
                        to="gifts.group",
                    ),
                ),
                (
                    "sender",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="invitation_dispatches",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
    ]
