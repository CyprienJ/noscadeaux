# Scheduled maintenance and diagnosis

[Operations](README.md)

The Compose `scheduler` runs these commands sequentially after a 30-second startup
delay, then sleeps for 3,600 seconds. A cycle therefore takes command runtime plus
one hour; it is not a wall-clock cron schedule.

## Scheduled commands

| Command | Effect |
| --- | --- |
| `send_notification_digests` | Sends due daily/weekly/30-day digests; updates `last_sent_at` |
| `send_event_reminders` | Sends birthday/Christmas subscription reminders; records delivery claims |
| `purge_deleted_shared_lists` | Permanently deletes lists soft-deleted at least 48 hours ago |
| `cleanup_unverified_users` | Deletes eligible incomplete, unverified registrations older than 30 minutes |
| `cleanup_auth_throttle_events` | Deletes auth-attempt rows older than the configured auth window |
| `cleanup_group_invitation_dispatches` | Deletes invitation dispatch rows older than their quota window |

The shell loop does not use `set -e`: one failing command does not prevent later
commands from running. A failure within a command can still stop its remaining
work for that cycle. Inspect logs for each command's outcome.

## Account cleanup eligibility

`cleanup_unverified_users` filters all of the following:

- `is_verified=False` and `date_joined` older than 30 minutes;
- neither staff nor superuser;
- `onboarding_version=0` and no onboarding completion timestamp.

Deletion cascades through related records. An already-onboarded person verifying
a replacement email is preserved. Cleanup runs hourly, so the 30-minute threshold
does not mean deletion happens exactly 30 minutes after signup.

## Other commands

| Command | Use |
| --- | --- |
| `audit_managed_members` | Read-only report of identities that migration 0044 cannot safely repair |
| `reset_demo` | Rebuilds demo data; `--lazy` skips a reset while it is still fresh |
| `validate_release_notes` | Validates the release-note files |

Demo login calls `reset_demo --lazy`; it is not one of the scheduler's recurring
commands. Audit results are described in [onboarding migration](onboarding-migration.md).

## Delivery and retry semantics

Invitation quotas count requested recipients, even if delivery fails; dispatch
rows contain counts, not recipient addresses. Verification email failures leave
the account available for retry. Reminder delivery claims are unique by
subscription/event/year and removed if sending raises an exception.

Digest preferences advance their last-send time when a digest is sent or when the
summary is empty. Reminders test dates against the current day (or `--date`), so an
extended outage may require an explicit date-based rehearsal/recovery run. A
recovery send is an external action, not a read-only diagnostic.

Do not enable verbose provider logs containing addresses or message payloads in
production. Token-bearing invitation, verification, feed, and restore paths also
need appropriate access-log treatment at the hosting/proxy layer; the repository's
Nginx configuration does not define a token-redacting log format.

## Common symptoms

| Symptom | Inspect |
| --- | --- |
| HTTP redirected to HTTPS locally | Export `DEV=True`; settings compare the exact string |
| `.env` value seems ignored | Django environment loading and Compose forwarding in [configuration](configuration.md) |
| Verification links point to the wrong host | Request host/proxy and `send_verification_email`; it does not use `PUBLIC_BASE_URL` |
| Invitation links point to the wrong host | Request host/protocol; `PUBLIC_BASE_URL` only for calls without a request |
| Digest or reminder links point to the wrong host | `PUBLIC_BASE_URL` in scheduler |
| Users loop through setup | `ONBOARDING_STEPS`, completion fields, pending token, allowed route names |
| Email/cleanup stops while website works | Scheduler container logs and database/schema readiness |
| Migration fails on a managed identity | Audit and resolve ambiguity on a database copy first |
| Static asset manifest errors | Built image, `collectstatic`, shared static volume, Nginx mount |
| Many users hit one IP limit | Trusted proxy chain and effective `X-Real-IP` |

## Sources

- [Command implementations](../../gifts/management/commands/).
- [Scheduler definition](../../docker-compose.yml).
- [Notification behavior](../features/notifications.md).
- [Configuration](configuration.md).
