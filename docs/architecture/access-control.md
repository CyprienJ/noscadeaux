# Access control and privacy boundaries

[Architecture](README.md)

Authorization depends on the kind of list, the requested operation, group context,
and recipient identity. The project does not have one universal permission class.
Use the policy helpers for the path being changed and read its tests.

## Entry points

| Surface | Gate / policy |
| --- | --- |
| Browser account pages | Django session authentication and HTTP-method decorators |
| Incomplete accounts | `AccountSetupMiddleware` and `ONBOARDING_STEPS` |
| Group invitations | `can_manage_group_invitations`: authenticated membership |
| Managed people | `can_manage_group_people`: active real member, same demo scope |
| Another person's list | Common group or registered Secret Santa assignment; optional group-context check |
| Shared-list management | `_active_member_list`: current member, list not deleted |
| Shared-list visitor | Valid group publication by a current shared-list member |
| Event page | Access token, with owner checks for management and session identity for guests |
| Extension API | Hashed bearer token for an active, verified user |

Group creators are not universally privileged over other members. Event ownership,
shared-list membership, and managed-person permissions are separate policies.

## Recipient privacy

Personal owners see their own wishes without reservation state; other people's
ideas for them are treated as surprises. Shared-list members are all recipients
and must not see buyer reservation/comment details for shared wishes.

Reservation and comment handlers perform their own object/group checks. Preserve
these when changing templates or reusing view helpers. A hidden button is not a
substitute for checking the mutation endpoint.

`visible_in`, `from_group`, shared publications, and event/draft flags affect
different query paths. A Secret Santa assignment authorizes a list view but does
not by itself grant the ordinary group-based reservation flow. Test the actual
route and actor rather than inferring permission from what another page displays.

## Tokens and request authentication

- Browser mutations generally use CSRF-protected forms or same-origin requests.
  Check each route's decorator and response type when adding a caller.
- Group previews do not join on GET. Acceptance uses POST.
- Verification links change verification state, not session identity.
- Private group links, short codes, event tokens, RSS tokens, and restoration
  tokens are different credentials with different checks and lifetimes.
- The extension's JSON POST routes deliberately use bearer/PKCE authentication
  instead of browser cookie authentication; do not copy their CSRF exemptions
  into session-authenticated views.

## Rate limits and external input

Registration and verification resend limits persist attempt rows in the database.
The stored IP value is an HMAC using the Django secret, with `X-Real-IP` preferred
over the direct remote address. The deployment must supply trusted proxy headers.
The auth limiter performs an insert and count; it is not a transactional quota
reservation like invitation delivery.

Invitation recipient quotas lock the sender and group before reserving counts.
Bug-report throttling is instead in process memory and is not shared across
Gunicorn workers. These mechanisms should not be described as interchangeable.

Django template auto-escaping protects ordinary text. Registration password-help
HTML is produced by Django's escaping-aware utilities; the template does not
force arbitrary help text to be safe. Keep untrusted text escaped in new surfaces.

## Demo and cleanup

Demo flags prevent mixing sample identities with real groups/lists in the guarded
flows. When adding an operation, inspect `has_same_demo_scope` and existing tests.
Account/group deletion also invokes cleanup signals; read those before changing
authorization or cascade behavior.

## Implementation and tests

- [group_permissions.py](../../gifts/group_permissions.py),
  [middleware.py](../../gifts/middleware.py), [demo.py](../../gifts/demo.py).
- [views.py](../../gifts/views.py): `_check_gift_access`, `_get_reservation_group`,
  `_validate_reservation_participants`, `_get_comment_group`.
- [shared_lists.py](../../gifts/shared_lists.py), [events.py](../../gifts/events.py).
- [Testing map](../development/testing.md): permission, CSRF, privacy, and isolation suites.
