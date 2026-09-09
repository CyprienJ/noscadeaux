# Onboarding migration and managed-identity repair

[Operations](README.md)

Use this guide when upgrading a database from before the onboarding changes.
It preserves the rollout information formerly embedded in the project README.
The changes should be delivered together with their views, middleware, templates,
commands, and translations.

## Migration groups

| Migrations | Purpose |
| --- | --- |
| `0039_backfill_managed_member_profiles` | Earlier backfill of missing managed profiles |
| `0039_user_onboarding`–`0041` | Onboarding state, profile completion, persisted pending invitations |
| `0042` | Separate private group invitation tokens and dispatch accounting |
| `0043` | Daily aggregate onboarding counts |
| `0044` | Repair managed profiles and technical users while retaining wishes/memberships |
| `0045` | Persistent authentication throttle events |
| `0046` | Onboarding completion/version consistency constraint |
| `0047` | Composite authentication-throttle lookup index |

Read the exact [migration files](../../gifts/migrations/) against the database's
current state. Historical migrations use `apps.get_model`, not current model
imports. Migration 0044 keeps its own frozen palette so later application changes
cannot change historical repair behavior.

Two migration filenames start with `0039`; they form a dependency chain, not
parallel alternatives. The earlier backfill selects the first group by primary
key for a missing profile, including users with several groups. Run the audit
before the entire upgrade: 0044 checks remaining orphan users and cannot detect
that ambiguity once the earlier migration has created a profile.

## What 0044 repairs

- An inactive managed user with no profile and exactly one group gets a linked
  `ManagedMember` using its nickname and a deterministic color.
- Managed identities receive unusable passwords; repaired accounts are verified.
- A managed profile without a user gets an inactive technical identity.
- Gifts linked to that profile are reassigned to the technical user.
- Membership and missing display colors are repaired.

An orphan managed user that is active or belongs to zero/multiple groups causes
an explicit `RuntimeError` before automatic repair. The migration does not guess
which group owns the person or silently discard the identity.

## Before deployment

1. Back up the database and uploaded media.
2. Restore a recent production database into an isolated PostgreSQL environment.
3. Run the read-only audit against that copy using the new code:

   ```bash
   uv run python manage.py audit_managed_members
   ```

4. Review reported user IDs, active flags, and group counts. Resolve ambiguous
   identities deliberately before applying migrations to production.
5. Rehearse migrations on the copy and compare users, groups, memberships, and
   gifts before and after.
6. Exercise registration, existing-account login, invitation continuation after
   email verification, create/join/skip group choices, and managed-person changes.
7. Check real-provider email links in both languages and browser/device contexts;
   check the main screens on mobile/desktop, by keyboard, and with a screen reader.

The audit evaluates group counts in one annotated query. A clean audit only covers
the ambiguity conditions it checks; it is not a substitute for a full migration
rehearsal and delivery checks.

## Automated coverage and limits

[test_onboarding_delivery.py](../../gifts/test_onboarding_delivery.py) builds
historical synthetic data, migrates from schema 0038 through the repair, reverses
schema, and reapplies migrations. It also exercises ambiguity failure and a round
trip to the latest migration graph. Related invitation migration tests are in
[test_group_invitations.py](../../gifts/test_group_invitations.py).

These are disposable test-database checks. They are not a rehearsal on a recent
production copy, and local SQLite tests do not prove PostgreSQL behavior under load.

## Rollback boundaries

Migration 0044's reverse function is a data no-op: repaired links are not undone.
Repairs are transactional under the migration and designed to tolerate reapplication,
but reversing schema can remove onboarding and invitation state.

For recovery, prefer compatible old code on the expanded schema or a validated
full-backup restore. Dropping new columns or rotating invitation tokens is not a
data-preserving rollback. Coordinate the scheduler with schema changes: its fixed
startup delay does not guarantee that web startup migrations have finished.
