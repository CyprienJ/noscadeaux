# Accounts and onboarding

[Features](README.md)

Registration asks for an email address and a password entered twice. Email is
normalized to lowercase and checked case-insensitively for duplicates. The login
backend also supports case-insensitive email authentication.

## Account setup

The ordered steps are defined once in `ONBOARDING_STEPS`:

| Step | Completion condition | User action |
| --- | --- | --- |
| Verify email | `is_verified` | Open the verification link |
| Profile | `profile_completed_at` is set | Choose a nickname; optionally enter birthday month/day and an avatar |
| Group | `onboarding_version` reaches the current version | Create a group, join one, or skip |

The middleware redirects incomplete accounts to their next step, while keeping
the step's allowed routes accessible. Staff admin routes, static/media paths,
and routes whose names start with `event_` have explicit exceptions.

An invitation can be remembered in the session and on the account. After email
and profile completion, it takes precedence over the generic group-choice screen.
The stored value identifies an invitation, rather than an arbitrary redirect URL.

## Verification and recovery

- Registration logs the new user in and attempts to send a verification email.
- Following a valid email link verifies the target account. It does not log that
  account in or replace another account's session.
- On another device, the person signs in before continuing setup.
- A delivery failure leaves the account available for retry and shows an error.
- Resending verification has both a per-account cooldown and a per-IP limit.
- Registration attempts, including invalid submissions, count toward the IP limit.
- Old unverified registrations are periodically removed; see the exact eligibility
  conditions in [maintenance](../operations/maintenance.md).

The form intentionally reports duplicate email addresses. Rate limiting reduces
automated probing but does not make registration responses anonymous.

## State invariants

A new account starts at version zero with no profile or onboarding completion
timestamp. Profile completion can precede completion of the group step. The
database constrains `onboarding_completed_at` to be present exactly when
`onboarding_version >= 1`.

Use `complete_onboarding()` and the central step resolver when changing the flow.
Do not independently infer the next page in each view. `User.save()` also stamps
completion for versioned accounts; queryset updates bypass that method.

Daily onboarding totals record registration, verification, profile completion,
group creation, joining, and skipping. They are aggregate counts, not individual
funnels or abandonment measurements. Demo users are excluded.

## Profiles and demo mode

Account settings allow nickname, email, birthday, avatar, and password changes.
Changing email requires verification again. Birthday stores month/day only and
requires a valid pair. Uploaded avatars and group images use resized image fields;
profile/group presets are checked against the available static assets.

The public demo logs into a generated demo identity and lazily resets its sample
data when old enough. Demo scope is separate from real accounts and groups. Demo
account/profile mutations and invitation email sending have dedicated restrictions.

## Implementation and tests

- [account.py](../../gifts/account.py): `register`, `validate_registration`,
  `verify_email_confirm`, `resend_verification`, `onboarding_profile`.
- [onboarding.py](../../gifts/onboarding.py): step definitions and pending invitations.
- [middleware.py](../../gifts/middleware.py): `AccountSetupMiddleware`.
- [forms.py](../../gifts/forms.py), [backends.py](../../gifts/backends.py),
  [auth_throttle.py](../../gifts/auth_throttle.py), [demo.py](../../gifts/demo.py).
- [test_onboarding.py](../../gifts/test_onboarding.py),
  [test_onboarding_delivery.py](../../gifts/test_onboarding_delivery.py),
  [test_turnstile.py](../../gifts/test_turnstile.py).

For historical account repair, read [onboarding migration](../operations/onboarding-migration.md).
