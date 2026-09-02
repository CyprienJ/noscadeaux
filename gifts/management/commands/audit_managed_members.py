from django.core.management.base import BaseCommand
from django.db.models import Count

from gifts.models import User


class Command(BaseCommand):
    help = (
        "Read-only. Lists managed identities that migration 0044 cannot repair "
        "automatically: a managed User with no ManagedMember profile that is "
        "active, or that belongs to zero or several groups. Run it on a copy of "
        "production before the deployment window."
    )

    def handle(self, *args, **options):
        orphans = User.objects.filter(is_managed=True, managed_member_profile__isnull=True).annotate(
            group_count=Count("gift_groups")
        )
        problems = []
        for user in orphans:
            group_count = user.group_count
            if user.is_active or group_count != 1:
                problems.append((user.pk, user.is_active, group_count))

        if not problems:
            self.stdout.write(self.style.SUCCESS("No ambiguous managed identities. Migration 0044 will proceed."))
            return

        self.stdout.write(
            self.style.WARNING(f"{len(problems)} ambiguous managed identity(ies) need manual review before migrating:")
        )
        for pk, is_active, group_count in problems:
            self.stdout.write(f"  user pk={pk} is_active={is_active} groups={group_count}")
        self.stdout.write(
            "Resolve these by hand (attach or remove) before deploying, otherwise migration 0044 raises RuntimeError."
        )
