from django.db import migrations, models
from django.utils import timezone


CURRENT_ONBOARDING_VERSION = 1


def mark_existing_verified_users_complete(apps, schema_editor):
    user_model = apps.get_model("gifts", "User")
    user_model.objects.filter(is_verified=True).update(
        onboarding_version=CURRENT_ONBOARDING_VERSION,
        onboarding_completed_at=timezone.now(),
    )


def reset_onboarding_state(apps, schema_editor):
    user_model = apps.get_model("gifts", "User")
    user_model.objects.update(onboarding_version=0, onboarding_completed_at=None)


class Migration(migrations.Migration):
    dependencies = [
        ("gifts", "0039_backfill_managed_member_profiles"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="onboarding_version",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="user",
            name="onboarding_completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="verification_email_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(mark_existing_verified_users_complete, reset_onboarding_state),
    ]
