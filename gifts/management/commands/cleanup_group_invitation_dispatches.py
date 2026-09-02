from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from gifts.models import GroupInvitationDispatch


class Command(BaseCommand):
    help = "Deletes group invitation dispatch rows older than the rate-limit window."

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(seconds=settings.GROUP_INVITATION_RATE_WINDOW_SECONDS)
        deleted, _ = GroupInvitationDispatch.objects.filter(created_at__lt=cutoff).delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} group invitation dispatches."))
