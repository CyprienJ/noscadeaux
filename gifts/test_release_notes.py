import tempfile
from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import User
from .onboarding import CURRENT_ONBOARDING_VERSION
from .release_notes import ReleaseNoteError, _load_release_notes, localized_release_notes, parse_version


def write_release(directory, version, title=None):
    Path(directory, f"{version}.toml").write_text(
        f'''version = "{version}"
date = 2026-08-18

[fr]
title = "{title or f"Nouveautés {version}"}"
content = "Contenu de la version {version}."

[en]
title = "Updates {version}"
content = "Content for version {version}."
''',
        encoding="utf-8",
    )


class VersionParsingTest(TestCase):
    def test_versions_are_compared_numerically(self):
        self.assertGreater(parse_version("1.10.0"), parse_version("1.9.0"))

    def test_invalid_version_is_rejected(self):
        with self.assertRaises(ReleaseNoteError):
            parse_version("1.2")


class ReleaseNotesTest(TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.addCleanup(_load_release_notes.cache_clear)
        _load_release_notes.cache_clear()

    def override_releases(self, version="1.2.0"):
        return override_settings(APP_VERSION=version, RELEASE_NOTES_DIR=Path(self.temporary_directory.name))

    def create_user(self, last_seen_version):
        return User.objects.create_user(
            email="alice@example.com",
            username="alice@example.com",
            nickname="Alice",
            password="secret-password",
            is_verified=True,
            onboarding_version=CURRENT_ONBOARDING_VERSION,
            profile_completed_at=timezone.now(),
            last_seen_version=last_seen_version,
        )

    def test_loader_uses_requested_language_and_orders_versions(self):
        write_release(self.temporary_directory.name, "1.10.0")
        write_release(self.temporary_directory.name, "1.2.0")

        with self.override_releases("1.10.0"):
            releases = localized_release_notes("en")

        self.assertEqual([release["version"] for release in releases], ["1.2.0", "1.10.0"])
        self.assertEqual(releases[0]["title"], "Updates 1.2.0")

    def test_unseen_notes_are_returned_only_once_and_mark_current_version(self):
        write_release(self.temporary_directory.name, "1.1.0")
        write_release(self.temporary_directory.name, "1.2.0")
        user = self.create_user("1.0.0")
        self.client.force_login(user)

        with self.override_releases():
            first_response = self.client.post(reverse("unseen_release_notes"))
            second_response = self.client.post(reverse("unseen_release_notes"))

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(
            [release["version"] for release in first_response.json()["releases"]],
            ["1.1.0", "1.2.0"],
        )
        self.assertEqual(second_response.json()["releases"], [])
        user.refresh_from_db()
        self.assertEqual(user.last_seen_version, "1.2.0")

    def test_first_visit_is_initialized_without_showing_history(self):
        write_release(self.temporary_directory.name, "1.2.0")
        user = self.create_user("")
        self.client.force_login(user)

        with self.override_releases():
            response = self.client.post(reverse("unseen_release_notes"))

        self.assertEqual(response.json()["releases"], [])
        user.refresh_from_db()
        self.assertEqual(user.last_seen_version, "1.2.0")

    def test_changelog_is_public_and_paginated_by_ten(self):
        for patch in range(1, 12):
            write_release(self.temporary_directory.name, f"1.0.{patch}")

        with self.override_releases("1.0.11"):
            response = self.client.get(reverse("changelog"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["page"]), 10)
        self.assertContains(response, "Nouveautés 1.0.11")
        self.assertNotContains(response, '<h2 class="h3 mb-3">Nouveautés 1.0.1</h2>', html=True)

    def test_future_last_seen_version_is_not_downgraded(self):
        user = self.create_user("2.0.0")
        self.client.force_login(user)

        with self.override_releases():
            response = self.client.post(reverse("unseen_release_notes"))

        self.assertEqual(response.json()["releases"], [])
        user.refresh_from_db()
        self.assertEqual(user.last_seen_version, "2.0.0")
