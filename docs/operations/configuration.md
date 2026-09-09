# Configuration reference

[Operations](README.md)

[config/settings.py](../../config/settings.py) defines runtime settings. It reads
environment variables explicitly; `.env` is not automatically loaded by Django.
Compose uses `.env` for interpolation but forwards only variables declared in each
service's `environment` section.

## Application environment

| Variable | Default / effect |
| --- | --- |
| `DEV` | `False`; only the string `True` enables Django debug mode |
| `DJANGO_SECRET_KEY` | Development fallback `DEFAULT_SECRET_KEY`; supply a private production value |
| `DATABASE_URL` | SQLite at repository-root `db.sqlite3` |
| `PUBLIC_BASE_URL` | `https://noscadeaux.fr`; scheduled-message links and request-less invitation fallback |
| `APP_VERSION` | `project.version` from `pyproject.toml` |
| `DEPLOYMENT_REVISION` | Empty; truncated to 100 characters at runtime |
| `DEMO_RESET_INTERVAL_MINUTES` | `15`; lazy demo reset age |
| `ANYMAIL_RESEND_API_KEY` | No key; required for the configured Resend backend |

## Registration and invitations

| Variable | Default |
| --- | --- |
| `TURNSTILE_SITE_KEY` | Empty |
| `TURNSTILE_SECRET_KEY` | Empty; nonempty enables registration challenge verification |
| `TURNSTILE_TIMEOUT` | `5` seconds |
| `AUTH_RATE_WINDOW_SECONDS` | `900` |
| `REGISTRATION_MAX_ATTEMPTS_PER_IP_WINDOW` | `10` |
| `VERIFICATION_RESEND_MAX_ATTEMPTS_PER_IP_WINDOW` | `10` |
| `GROUP_INVITATION_MAX_RECIPIENTS_PER_REQUEST` | `10` |
| `GROUP_INVITATION_MAX_RECIPIENTS_PER_USER_WINDOW` | `30` |
| `GROUP_INVITATION_MAX_RECIPIENTS_PER_GROUP_WINDOW` | `100` |
| `GROUP_INVITATION_RATE_WINDOW_SECONDS` | `3600` |

The per-account verification resend cooldown is a fixed Django setting of 60
seconds, not an environment override. Auth limits count attempt records; invitation
limits count requested recipients. Cleanup commands use the matching window.

## Public bug reporting

| Variable | Default / effect |
| --- | --- |
| `BUG_REPORT_REPOSITORY` | Empty; expected `owner/repository` |
| `BUG_REPORT_TOKEN` | Empty; server-side token with issue creation access |
| `BUG_REPORT_LABELS` | `bug,source:user-report,status:unconfirmed` |
| `GITHUB_API_URL` | `https://api.github.com` |
| `GITHUB_API_VERSION` | `2022-11-28` |
| `BUG_REPORT_TIMEOUT` | `10` seconds |

The configured reporting repository is intended to be public. Never put the
reporting token into browser assets. [.env.example](../../.env.example) includes
placeholders for this integration, Turnstile, and the public proxy/domain.

## Compose inputs and forwarding

| Compose input | Destination |
| --- | --- |
| `POSTGRES_PASSWORD` | Database password and interpolated web/scheduler database URL |
| `DJANGO_SECRET_KEY` | Web and scheduler |
| `RESEND_API_KEY` | Web/scheduler `ANYMAIL_RESEND_API_KEY` |
| `PUBLIC_BASE_URL` | Web and scheduler |
| `BUG_REPORT_*` | Web only |
| `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY` | Web only |
| `NPM_NETWORK` | External proxy network name; default `proxy` |

Compose fixes `DEV=False`. It does not currently forward the auth/invitation limit
overrides, demo reset interval, or HTTP timeout overrides listed above. To use
those in containers, add them to the applicable service environment. Forward
window changes to both web and scheduler so accounting and cleanup agree.

## Fixed Django settings worth checking

- Hosts: `noscadeaux.fr`, `www.noscadeaux.fr`, `127.0.0.1`, `localhost`.
- CSRF trusted origins: the HTTPS production domains.
- Default language: French; supported languages: French and English; timezone: UTC.
- Email backend: Resend through Anymail; sender: `Nos Cadeaux <noreply@noscadeaux.fr>`.
- Sessions: secure cookies outside debug, HTTP-only session cookie, 30-day age.
- HTTPS detection: `X-Forwarded-Proto`; HTTPS redirect outside debug/test commands.
- Media root: `media/`; static collection root: `staticfiles/`.

These are not automatically overridden by similarly named environment variables.
Use a settings module change or an explicit local override when needed.

Nginx passes the request host and forwarded protocol and overwrites `X-Real-IP`.
With multiple proxies, verify which address reaches that header: authentication
throttling uses it, and an upstream proxy address can group multiple visitors
under one limit.
