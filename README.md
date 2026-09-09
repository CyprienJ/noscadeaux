# nosCadeaux

[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=CyprienJ_noscadeaux&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=CyprienJ_noscadeaux)
[![Security Rating](https://sonarcloud.io/api/project_badges/measure?project=CyprienJ_noscadeaux&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=CyprienJ_noscadeaux)
[![Maintainability Rating](https://sonarcloud.io/api/project_badges/measure?project=CyprienJ_noscadeaux&metric=sqale_rating)](https://sonarcloud.io/summary/new_code?id=CyprienJ_noscadeaux)

nosCadeaux is a Django web application for organizing gift wishes with family and
friends. People share lists through groups, suggest surprises, coordinate who buys
what, and track shared expenses. The interface is available in French and English.

**Website:** [noscadeaux.fr](https://noscadeaux.fr)

## How it works

1. Create an account, verify your email, and choose a nickname.
2. Create or join a group, or continue with a personal list first.
3. Add wishes to your list and choose the groups in which to share them.
4. Browse other members' wishes, suggest surprises, and reserve gifts together.
5. Record gifts as offered or received; optionally track group history and balances.

Groups can include children or relatives without accounts. Shared lists let several
people manage wishes together. Event lists support visitors without accounts and
include a Secret Santa mode. A Firefox extension can capture a product from a shop
page, let you review it, and add it to your personal list.

The application renders HTML on the server with Django templates and adds browser
interactions with JavaScript. Django stores accounts, lists, and reservations in a
relational database. Production uses PostgreSQL, Gunicorn, and Nginx in Docker;
local development defaults to SQLite. A separate scheduler runs email and cleanup
commands. Dependencies are managed with uv.

## Documentation: choose a branch

Read this page first, then open only the branch relevant to your task. Each branch
starts with an overview and links to narrower guides and implementation files.

| Branch | What you will find |
| --- | --- |
| [Features and workflows](docs/features/README.md) | Accounts, groups, list types, gift coordination, events, notifications |
| [Architecture](docs/architecture/README.md) | Request flow, module map, data relationships, access rules, integrations |
| [Development](docs/development/README.md) | Local setup, focused tests, translations, versioning and release notes |
| [Operations](docs/operations/README.md) | Deployment, configuration, maintenance, migration rehearsal |

### Quick routes

| Task | Start here |
| --- | --- |
| Run the project locally | [Development setup](docs/development/README.md) |
| Change registration or onboarding | [Account lifecycle](docs/features/accounts.md) |
| Change who can see or modify a gift | [Access control](docs/architecture/access-control.md) |
| Change shared wishes | [Shared lists](docs/features/shared-lists.md) |
| Work on product capture | [Extension protocol](docs/architecture/extension.md) |
| Diagnose email or scheduled cleanup | [Maintenance](docs/operations/maintenance.md) |
| Deploy onboarding changes to an older database | [Onboarding migration](docs/operations/onboarding-migration.md) |

## Reading and maintaining these docs

These guides serve both people and AI agents. Use the linked source files and
named functions for exact implementation details instead of loading the whole
repository. The docs explain behavior and important constraints; source code,
migrations, and tests remain the reference when they disagree.

Keep one main topic per page, link to its parent, and link onward for deeper detail.
Aim for about 200 lines per file; modest overruns are fine when they keep a topic
coherent. Update the relevant leaf page when behavior changes and its parent when
the navigation changes. Avoid copying the same detailed explanation into several
pages. Documentation is in English; the product remains bilingual.

## License

GNU Affero General Public License v3.0 or later (`AGPL-3.0-or-later`).
See [LICENSE](LICENSE).
