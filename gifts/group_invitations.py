import logging
from dataclasses import dataclass
from datetime import timedelta
from smtplib import SMTPException
from urllib.parse import urljoin

from anymail.exceptions import AnymailError
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import transaction
from django.db.models import Sum
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from gifts.models import Group, GroupInvitationDispatch, User

logger = logging.getLogger(__name__)

# Exceptions an email backend is expected to raise while delivering a message.
# BadHeaderError (a ValueError) is deliberately excluded so a malformed header
# surfaces instead of being silently counted as a delivery failure.
EMAIL_SEND_ERRORS = (SMTPException, AnymailError, OSError)


class InvitationRateLimitError(Exception):
    pass


@dataclass(frozen=True)
class InvitationSendResult:
    requested_count: int
    sent_count: int
    failed_count: int


def build_group_invitation_url(group, request=None):
    """Absolute URL for the private invitation link.

    When a request is available (the invitations screen, sending emails from a
    view) the link is built on the host the user is actually on, exactly like the
    e-mail verification link. `PUBLIC_BASE_URL` is only the fallback for
    request-less contexts such as background jobs, and keeps local development
    from handing out links that point at production.
    """
    path = reverse("group_invitation", kwargs={"token": group.invitation_token})
    if request is not None:
        return request.build_absolute_uri(path)
    return urljoin(f"{settings.PUBLIC_BASE_URL.rstrip('/')}/", path.lstrip("/"))


def _used_recipient_quota(queryset):
    return queryset.aggregate(total=Sum("requested_count"))["total"] or 0


def _reserve_invitation_quota(group, sender, requested_count):
    if requested_count > settings.GROUP_INVITATION_MAX_RECIPIENTS_PER_REQUEST:
        raise InvitationRateLimitError

    window_start = timezone.now() - timedelta(seconds=settings.GROUP_INVITATION_RATE_WINDOW_SECONDS)
    with transaction.atomic():
        User.objects.select_for_update().get(pk=sender.pk)
        locked_group = Group.objects.select_for_update().get(pk=group.pk)
        recent_dispatches = GroupInvitationDispatch.objects.filter(created_at__gte=window_start)
        user_total = _used_recipient_quota(recent_dispatches.filter(sender=sender))
        group_total = _used_recipient_quota(recent_dispatches.filter(group=locked_group))
        if (
            user_total + requested_count > settings.GROUP_INVITATION_MAX_RECIPIENTS_PER_USER_WINDOW
            or group_total + requested_count > settings.GROUP_INVITATION_MAX_RECIPIENTS_PER_GROUP_WINDOW
        ):
            raise InvitationRateLimitError
        return GroupInvitationDispatch.objects.create(
            group=locked_group,
            sender=sender,
            requested_count=requested_count,
        )


def send_group_invitations(group, sender, recipients, request=None):
    """Send one private message per recipient over a single backend connection,
    retaining no recipient address and logging failures by exception type only."""
    dispatch = _reserve_invitation_quota(group, sender, len(recipients))
    invitation_url = build_group_invitation_url(group, request)
    context = {
        "group": group,
        "inviter_name": sender.nickname or sender.email,
        "invitation_url": invitation_url,
    }
    subject = _("Invitation to join %(group_name)s") % {"group_name": group.name}
    message_txt = render_to_string("emails/group_invitation.txt", context)
    message_html = render_to_string("emails/group_invitation.html", context)
    sent_count = 0

    connection = get_connection()
    try:
        # Opening the connection is a delivery step like sending: a backend that
        # is entirely down fails every recipient rather than raising a 500.
        connection.open()
    except EMAIL_SEND_ERRORS as error:
        logger.warning("Group invitation backend unavailable: %s", type(error).__name__)
    else:
        try:
            for recipient in recipients:
                message = EmailMultiAlternatives(
                    subject,
                    message_txt,
                    settings.DEFAULT_FROM_EMAIL,
                    [recipient],
                    connection=connection,
                )
                message.attach_alternative(message_html, "text/html")
                try:
                    sent_count += message.send()
                except EMAIL_SEND_ERRORS as error:
                    logger.warning("Group invitation email failed: %s", type(error).__name__)
        finally:
            connection.close()

    failed_count = len(recipients) - sent_count
    dispatch.sent_count = sent_count
    dispatch.failed_count = failed_count
    dispatch.save(update_fields=["sent_count", "failed_count"])
    return InvitationSendResult(
        requested_count=len(recipients),
        sent_count=sent_count,
        failed_count=failed_count,
    )
