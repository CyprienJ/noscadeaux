# Development

[Project overview](../../README.md)

Use a local checkout and a development database for the commands below. Python
3.12+ and uv are required. Node.js 22 matches the JavaScript test job. GNU gettext
is needed when extracting or compiling translations. Exact Python dependencies
are declared in [pyproject.toml](../../pyproject.toml) and resolved in
[uv.lock](../../uv.lock).

## Start locally

From the repository root:

```bash
uv sync --dev --frozen --no-build
export DEV=True
export PUBLIC_BASE_URL=http://127.0.0.1:8000
uv run python manage.py migrate
uv run python manage.py runserver 127.0.0.1:8000
```

Open `http://127.0.0.1:8000/`. Django routes browser pages to a supported language
prefix. `DEV=True` enables debug mode, local static/media serving, and HTTP cookies
instead of the production HTTPS settings.

Without `DATABASE_URL`, the app uses `db.sqlite3` in the repository root. If you
already have that file, migrations apply to that database. Choose a separate
database URL when you need an isolated dataset.

Settings read the process environment with `os.environ`; Django does not load
`.env` automatically. The example file documents selected integrations, not every
setting. Export variables or configure your IDE's environment explicitly.

## Email during local development

The default backend sends through Resend. To exercise registration locally without
sending real email, use a temporary settings module outside the repository:

```bash
cat > /tmp/noscadeaux_dev_settings.py <<'PY'
from config.settings import *

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
PY
PYTHONPATH="$PWD:/tmp" uv run python manage.py runserver \
  127.0.0.1:8000 --settings=noscadeaux_dev_settings
```

Keep `DEV=True` exported. Verification messages appear in the terminal. Verification
links currently hard-code HTTPS, so use the same link with `http://127.0.0.1:8000`
as its origin when testing against this HTTP development server.

Leave `TURNSTILE_SECRET_KEY` empty for local registration without the external
challenge. The public demo is another way to inspect sample data without the
registration path; its state resets lazily when its configured interval expires.

## Administration and files

Create a local administrator with:

```bash
uv run python manage.py createsuperuser
```

The admin uses Django Unfold and is under the language-prefixed admin route.
Staff have an admin-route exception in account-setup middleware.

Edit templates in `gifts/templates/` and source assets in `gifts/static/`.
`staticfiles/` is collected output; uploaded media is under `media/`. Both are
separate from application source. No frontend bundling step is configured for
the Django pages.

## Find the smallest relevant area

1. Read the relevant [feature guide](../features/README.md).
2. Use its linked modules and named functions to locate the behavior.
3. Check the matching tests before changing access or lifecycle rules.
4. Update that guide if the behavior changes.

In an IDE, use the project's configured interpreter rather than a system Python.
The shell examples use uv to select the project environment.

## Continue

- [Testing and checks](testing.md): suite map and validation commands.
- [Translations and releases](translations-releases.md): text, catalogs, versions, release notes.
- [Architecture](../architecture/README.md): request flow and module responsibilities.
- [Firefox setup](../../firefox-extension/README.md): load and test the extension.
- [Operations](../operations/README.md): production services and configuration.
