from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gifts", "0040_user_profile_completed_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="pending_group_invite_token",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
    ]
