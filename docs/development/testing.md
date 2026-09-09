# Testing and checks

[Development](README.md)

The project uses Django's test runner and Node's built-in test runner. Python
style checks use Ruff. Run commands from the repository root after syncing the
development dependencies.

## Standard checks

```bash
uv run --frozen --no-sync --no-build ruff check .
uv run --frozen --no-sync --no-build ruff format --check .
uv run --frozen --no-sync --no-build python manage.py validate_release_notes
uv run --frozen --no-sync --no-build python manage.py test
node --test firefox-extension/tests/*.test.js
node --test gifts/tests_js/*.test.cjs
```

These correspond to the checks in [ci-cd.yml](../../.github/workflows/ci-cd.yml).
The workflow also checks that the PR's application version is greater than the
target branch's version. See [versioning](translations-releases.md).

Ruff targets Python 3.12 with a 120-character line length. Its configuration
excludes migrations. This exclusion does not remove the need to review and
exercise data migrations.

## Focused suites

| Change area | Test module or class |
| --- | --- |
| Registration, profile, redirects, throttles | `gifts.test_onboarding` |
| Email failure/recovery and migration rehearsal | `gifts.test_onboarding_delivery` |
| Group previews, invitation delivery, rotation | `gifts.test_group_invitations` |
| Managed people, deletion cascades, audit queries | `gifts.test_managed_members` |
| Shared publications, privacy, transfer, restore | `gifts.test_shared_lists` |
| Turnstile registration | `gifts.test_turnstile` |
| Extension authorization and quick-add | `gifts.test_extension_api` |
| Public issue reporting | `gifts.test_bug_reports` |
| Release notes and version metadata | `gifts.test_release_notes`, `gifts.test_version` |
| Personal list access | `gifts.tests.GiftAccessControlTest` |
| Reservations and comments | `gifts.tests.ReservationFlowTest`, `gifts.tests.GiftCommentTest` |
| Offered gifts and balances | `gifts.tests.OfferGiftTest`, `gifts.tests.ComputeGroupBalancesTest` |
| Notifications | `gifts.tests.SubscriptionTest` |
| Events and Secret Santa | `gifts.tests.EventListCRUDTest`, `gifts.tests.GuestReservationTest`, `gifts.tests.SecretSantaEventTest` |
| Demo isolation | `gifts.tests.PublicDemoTest` |

Example for a targeted change:

```bash
uv run python manage.py test gifts.test_shared_lists
uv run python manage.py test gifts.tests.ReservationFlowTest gifts.tests.GiftCommentTest
```

[gifts/tests.py](../../gifts/tests.py) contains additional suites; use class names
to narrow a run. [gifts/tests_js/](../../gifts/tests_js/) covers invitation browser
behavior; [firefox-extension/tests/](../../firefox-extension/tests/) covers product
extraction and extension background behavior.

## Test environment behavior

Django creates a test database. Test settings select ordinary static-file storage
and disable the production HTTPS redirect when `test` is in the command line.
Email tests use Django's test backend or explicit overrides; external HTTP calls
are mocked in their respective suites.

`create_users()` in `gifts/tests.py` is a common fixture helper. New test users may
need verification, profile, and onboarding state explicitly set to reach a target
view without middleware redirecting them.

Migration suites use `MigrationExecutor` and historical models, then restore the
latest schema. Run them against the test database, not by manually reversing a
working database. SQLite success is not evidence of PostgreSQL lock behavior.

## Useful regression cases

For access changes, exercise owner, group member, outsider, shared-list member,
managed-person actor, and demo scope where relevant. Check direct POST requests,
not just button visibility. Test recipient privacy and CSRF behavior explicitly.

For ORM performance, a query-count assertion with several records exposes N+1
regressions. For template escaping, test both malicious ordinary text and any
intentionally generated safe markup that must retain its formatting.

For documentation-only changes, check links, navigation, code references, and
page size; the application suite need not be rerun unless executable code changes.
