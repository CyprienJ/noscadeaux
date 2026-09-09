# External services

[Architecture](README.md)

The server integrates with Resend through Django Anymail, Cloudflare Turnstile,
and GitHub Issues. The Firefox integration has its own [protocol guide](extension.md).
Runtime settings are listed in [configuration](../operations/configuration.md).

## Email

`EMAIL_BACKEND` is `anymail.backends.resend.EmailBackend`. The server reads
`ANYMAIL_RESEND_API_KEY`; Compose maps its `RESEND_API_KEY` input into that name.
Messages generally have text and HTML versions in
[templates/emails/](../../gifts/templates/emails/).

Email triggers include verification, password reset, group invitations, ordinary
new-wish additions, shared-list changes, digests, and reminders. Some sends happen
within requests; others use `transaction.on_commit` or scheduled commands.
Failure handling differs by caller, so an email-related change should inspect
the originating workflow.

Verification catches SMTP, Anymail, and OS errors, reports a retryable failure,
and stamps the sent time only on success. Invitation sending reuses one backend
connection, isolates each recipient, and records partial results. Its logs retain
exception types rather than recipient addresses or provider payloads.

Invitation links built from a browser request use its host and protocol;
`PUBLIC_BASE_URL` is the fallback when no request is supplied. Digests and reminders
also use `PUBLIC_BASE_URL`. Verification and some request-driven links use Django's
`get_current_site(request)`. The Sites app is not enabled in current settings, so
this resolves from the request host. Verification currently forces HTTPS. Check
the actual link builder when changing domains or proxy headers.

For local email testing, use a console backend through a local settings override;
the project does not currently read an `EMAIL_BACKEND` environment variable.
See [development setup](../development/README.md).

## Cloudflare Turnstile

Registration enables Turnstile when `TURNSTILE_SECRET_KEY` is nonempty. A matching
public site key is used by the template. The backend verifies the submitted token
with Cloudflare and expects the `register` action. Failed or unavailable
verification prevents account creation and leaves an error on the form.

The integration lives in [turnstile.py](../../gifts/turnstile.py) and is called by
registration validation in [account.py](../../gifts/account.py). Tests mock the
provider in [test_turnstile.py](../../gifts/test_turnstile.py).

## Public bug reports to GitHub

The public bug-report form creates an issue directly in the configured repository.
No report model is stored in the application database. The repository is intended
to be public so the resulting issue can be read without a GitHub account.

Configure `BUG_REPORT_REPOSITORY`, `BUG_REPORT_TOKEN`, and optionally labels. The
server token needs issue creation permission on that repository and stays on the
server. The form collects a description, reproduction/context fields, and applies
input checks and a honeypot. Ticket metadata includes the deployed application
version and revision; route details are sanitized before being included.

The rate limiter allows three accepted attempts per ten-minute window per client
key in process memory. It is local to each worker. Failures keep the entered form
text available for retry. The GitHub response is validated before using its issue
number in the success page.

Sources: [bug_reports.py](../../gifts/bug_reports.py),
[bug_report.js](../../gifts/static/gifts/bug_report.js),
[test_bug_reports.py](../../gifts/test_bug_reports.py).
