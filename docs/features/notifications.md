# Notifications and subscriptions

[Features](README.md)

Notifications combine per-person subscriptions, personal digest preferences,
private RSS feeds, and scheduled reminders. They are separate mechanisms with
different delivery triggers.

## Channels

| Channel | Trigger | Stored preference |
| --- | --- | --- |
| New-wish email | A qualifying addition through `add_gift` | `Subscription.email_enabled` |
| RSS | Feed reader requests a private URL | `Subscription.rss_enabled` and `feed_token` |
| Digest | Scheduler finds a due preference | `NotificationDigestPreference.frequency` |
| Birthday / Christmas | Scheduler finds the selected reminder date | Fields on `Subscription` |

The notification center lets users manage subscriptions and digest frequency.
Subscribing requires the applicable list access checks. Email templates have
plain-text and HTML versions under `gifts/templates/emails/`.

## New-wish emails and RSS

The ordinary `add_gift` path sends immediate emails for eligible, non-draft gifts.
It checks subscriber membership and selected visibility and skips the actor and
demo recipients. Do not assume every `Gift.objects.create()` sends a notification:
this behavior lives in the view, not in a universal gift-creation signal.

Each RSS feed belongs to one subscriber/owner pair. Its URL token grants feed
access without login, but the handler rechecks common group membership and that
RSS is enabled. It returns up to 50 recent eligible, non-draft, non-offered personal
gifts, excluding shared and event gifts.

Unsubscribe links identify a subscriber and list owner using a signed-token flow.
Feed and unsubscribe links should not be published as ordinary public URLs.

## Digests

Digest frequencies are disabled, daily, weekly, or monthly; monthly currently means
a 30-day interval. Due active, verified users receive a summary of recent group
wishes, current reservations, upcoming birthdays, and group balances.

The command records `last_sent_at` even when there is nothing to send. Digest
selection is its own query, so changes to gift visibility should be reviewed here
as well as on list pages and feeds.

## Reminders

Birthday and Christmas reminders use each subscription's lead time. Default lead
times are 14 and 30 days respectively. Birthdays use month/day, without a birth year.

`ReminderDelivery` prevents duplicate sends for a subscription, event type, and
event year. A send failure deletes the delivery claim and raises the error so a
retry is possible. The optional `--date YYYY-MM-DD` command argument is useful for
rehearsal. Despite its name, `send_event_reminders` handles birthday/Christmas
subscriptions, not arbitrary `EventList.event_date` reminders.

## Implementation and tests

- [views.py](../../gifts/views.py): `toggle_subscription`, `notification_center`,
  `unsubscribe_token`, `add_gift`.
- [feeds.py](../../gifts/feeds.py): `SubscriptionFeed`.
- [send_notification_digests.py](../../gifts/management/commands/send_notification_digests.py).
- [send_event_reminders.py](../../gifts/management/commands/send_event_reminders.py).
- [tests.py](../../gifts/tests.py): `SubscriptionTest`.
- [Maintenance](../operations/maintenance.md): scheduler and retry considerations.
