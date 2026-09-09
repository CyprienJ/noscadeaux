# Event lists and Secret Santa

[Features](README.md)

An event list is a token-accessible page for a particular occasion. It has an
owner, description, optional date/image, and a mode: wishlist or Secret Santa.
It is separate from group membership and personal/shared-list publication.

## Access and guest identity

The event URL contains `EventList.access_token`, currently generated as eight
uppercase hexadecimal characters. Rotating it changes the access URL. This is
different from the longer group invitation token.

Visitors can open the event without an account. Before reserving, they either
use their authenticated identity or enter a guest name stored in the session.
Anonymous reservations are associated with a session key; a name by itself is
not an authenticated account. Authenticated visitors are added to the event's
participants so it can appear on their dashboard.

Owner actions manage event details, wishes, visibility, photos, and tokens. Hidden
event wishes are excluded from visitor queries. Authenticated users are checked
against the event's demo scope. Event routes also have explicit exceptions in
the account-setup middleware.

## Reservations

`GuestReservation` stores a gift, display name, optional user, session key, and
exclusivity flag. The reserve endpoint toggles the current identity's reservation:
an existing row is removed; otherwise a row is created. Do not assume it uses the
same participant validation or group locking as ordinary reservations.

Event owners can transfer an event gift to their personal list through a dedicated
action. It removes guest reservations, clears the event relation, and sets
visibility to the owner's groups when any exist.

## Secret Santa

Secret Santa mode adds a maximum budget, participants, exclusions, and stored draw
assignments. Participants combine registered users (including the owner) and
named `SecretSantaGuestParticipant` records added by the owner.

Participant keys use `user:<id>` or `guest:<id>`. Exclusions are directed pairs:
a particular giver must not receive a particular receiver. The draw shuffles with
`secrets.SystemRandom` and backtracks to find one receiver per giver, excluding
self-assignment and prohibited pairs.

If no valid assignment exists, the operation reports failure. A successful draw
replaces previous assignments atomically. Registered receivers can have a linked
personal wish list; named guests do not. A registered giver's assignment can
authorize viewing the receiver's personal list without a common group; it does
not automatically grant ordinary group reservation rights.

## Implementation and tests

- [events.py](../../gifts/events.py): `event_detail`, `_get_guest_identity`,
  `reserve_event_gift`, `_draw_secret_santa`, `draw_secret_santa`.
- [models.py](../../gifts/models.py): `EventList`, `GuestReservation`,
  `SecretSantaGuestParticipant`, `SecretSantaExclusion`, `SecretSantaAssignment`.
- [tests.py](../../gifts/tests.py): `EventListCRUDTest`, `EventDetailViewTest`,
  `GuestReservationTest`, `SecretSantaEventTest`, `EventTransferAndLeaveTest`.
