from django.db import migrations, models
from django.utils import timezone


def mark_existing_profiles_complete(apps, schema_editor):
    user_model = apps.get_model("gifts", "User")
    user_model.objects.filter(onboarding_version__gte=1).update(profile_completed_at=timezone.now())


def reset_profile_state(apps, schema_editor):
    user_model = apps.get_model("gifts", "User")
    user_model.objects.update(profile_completed_at=None)


class Migration(migrations.Migration):
    dependencies = [
        ("gifts", "0039_user_onboarding"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="profile_completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(mark_existing_profiles_complete, reset_profile_state),
    ]
