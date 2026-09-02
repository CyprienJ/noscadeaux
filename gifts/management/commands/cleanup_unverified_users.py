from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from gifts.models import User


class Command(BaseCommand):
    help = "Deletes unverified users older than 30 minutes"

    def handle(self, *args, **options):
        expiry_time = timezone.now() - timedelta(minutes=30)
        unverified_users = User.objects.filter(
            is_verified=False,
            date_joined__lt=expiry_time,
            is_staff=False,
            is_superuser=False,
            onboarding_completed_at__isnull=True,
            onboarding_version=0,
        )
        count = unverified_users.count()
        unverified_users.delete()
        self.stdout.write(self.style.SUCCESS(f"Successfully deleted {count} unverified users."))
