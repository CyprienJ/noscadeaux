# Translations, versions, and release notes

[Development](README.md)

The product supports French and English. Documentation is maintained separately
from user-visible interface translations.

## User-visible text

Use Django translation functions in Python and `trans` / `blocktrans` in templates.
Use lazy translations for declarations evaluated at import time when appropriate.
For JavaScript embedded in templates, preserve the existing escaping-aware
translation pattern instead of interpolating raw translated text into strings.

Whenever user-visible text changes, update the catalogs and compile them:

```bash
uv run python manage.py makemessages -l fr -l en \
  --ignore='.venv/*' --ignore='staticfiles/*' --ignore='media/*'
uv run python manage.py compilemessages
```

Review [French](../../locale/fr/LC_MESSAGES/django.po) and
[English](../../locale/en/LC_MESSAGES/django.po) entries and their compiled `.mo`
files. The Docker build also compiles messages using GNU gettext. Firefox has
its own [locale catalogs](../../firefox-extension/_locales/).

Use named URL reversal for language-prefixed pages. Root browser routes are
wrapped by `i18n_patterns`; the extension API is outside that wrapper. Check both
languages for redirects, email links, and text used inside browser scripts.

## Application version

The source of truth is `project.version` in [pyproject.toml](../../pyproject.toml),
using `X.Y.Z`. Bump according to the change:

```bash
uv version --bump patch
# Or --bump minor / --bump major as appropriate.
```

Keep resolved project metadata consistent with the lockfile. CI compares the PR
version to the target branch and rejects equal or lower versions. When the branch
already contains a suitable release bump, review that comparison before adding
another increment just for an extra commit.

The build injects `APP_VERSION` and `DEPLOYMENT_REVISION` into the Docker image and
OCI labels. Runtime settings otherwise fall back to the project version and an
empty revision. The production image is currently tagged `latest`.

## Release-note files

User-facing updates are TOML files under [gifts/release_notes/](../../gifts/release_notes/).
The filename stem and `version` must agree. French content is required; English
is optional and missing translations fall back to available content.

Example file `X.Y.Z.toml`, replacing the placeholder with the actual version:

```toml
version = "1.4.0"
date = 2026-09-02

[fr]
title = "Titre de la nouveauté"
content = "Description affichée dans la modale et le changelog."

[en]
title = "Update title"
content = "Description shown in the modal and changelog."
```

The date is a TOML date, not a quoted string. Titles and content must be nonempty.
Validate with:

```bash
uv run python manage.py validate_release_notes
```

[release_notes.py](../../gifts/release_notes.py) parses, validates, sorts, and caches
the files. The changelog shows localized notes; the unseen-notes endpoint uses
`User.last_seen_version` and the application version to choose updates. Registration
starts a new user at the current application version.

Tests: [test_release_notes.py](../../gifts/test_release_notes.py),
[test_version.py](../../gifts/test_version.py), and translation cases in
[tests.py](../../gifts/tests.py).
