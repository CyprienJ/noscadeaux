from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from gifts.models import AuthThrottleEvent


class Command(BaseCommand):
    help = "Deletes auth throttle events older than the rate-limit window."

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(seconds=settings.AUTH_RATE_WINDOW_SECONDS)
        deleted, _ = AuthThrottleEvent.objects.filter(created_at__lt=cutoff).delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} auth throttle events."))
