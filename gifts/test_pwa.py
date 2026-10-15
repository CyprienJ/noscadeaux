from django.contrib.staticfiles import finders
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation

from .models import User


class PublicPwaTests(SimpleTestCase):
    def test_manifest_is_language_independent(self):
        for language in ("fr", "en"):
            with translation.override(language):
                self.assertEqual(reverse("pwa_manifest"), "/manifest.webmanifest")
                response = self.client.get(reverse("pwa_manifest"))
                self.assertEqual(response["Content-Type"], "application/manifest+json")
                manifest = response.json()
                self.assertEqual(manifest["scope"], "/")
                self.assertEqual(manifest["start_url"], "/")
                self.assertTrue(manifest["icons"])
                for icon in manifest["icons"]:
                    self.assertTrue(finders.find(icon["src"].removeprefix("/static/")))

    @override_settings(TWA_SHA256_FINGERPRINTS=[])
    def test_unconfigured_association_is_empty(self):
        self.assertEqual(self.client.get(reverse("twa_assetlinks")).json(), [])

    @override_settings(TWA_SHA256_FINGERPRINTS=["AA:BB"])
    def test_association_has_configured_certificate(self):
        response = self.client.get("/.well-known/assetlinks.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json()[0]["target"]["package_name"], "com.cyprienj.noscadeaux")
        self.assertEqual(response.json()[0]["target"]["sha256_cert_fingerprints"], ["AA:BB"])

    def test_worker_is_served_at_root_with_revalidation(self):
        response = self.client.get(reverse("pwa_service_worker"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        self.assertEqual(response["Cache-Control"], "no-cache")
        self.assertTrue(response["Content-Type"].startswith("text/javascript"))

    def test_public_deletion_page_is_available_in_both_languages(self):
        for language in ("fr", "en"):
            response = self.client.get(f"/{language}/account-deletion/")
            self.assertContains(response, "mailto:cyprien.jorant@pm.me")
            self.assertNotContains(response, 'action="/' + language + '/account/delete/"')


class PwaOnboardingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pwa", email="pwa@example.com", password="test-password")
        self.client.force_login(self.user)

    def test_resources_remain_available_through_each_onboarding_step(self):
        for stage in ("verification", "profile", "group"):
            if stage == "profile":
                self.user.is_verified = True
            if stage == "group":
                self.user.profile_completed_at = timezone.now()
            self.user.save()
            for name in ("pwa_manifest", "pwa_service_worker", "pwa_offline", "twa_assetlinks", "account_deletion"):
                with self.subTest(stage=stage, name=name):
                    self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_offline_page_has_no_account_data(self):
        authenticated = self.client.get(reverse("pwa_offline"))
        self.client.logout()
        anonymous = self.client.get(reverse("pwa_offline"))
        self.assertEqual(authenticated.content, anonymous.content)
        self.assertNotContains(authenticated, self.user.email)

    def test_deletion_still_requires_post_and_csrf(self):
        self.assertEqual(self.client.get(reverse("delete_account")).status_code, 405)
        protected_client = Client(enforce_csrf_checks=True)
        protected_client.force_login(self.user)
        self.assertEqual(protected_client.post(reverse("delete_account")).status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    def test_incomplete_user_can_delete_account(self):
        response = self.client.post(reverse("delete_account"))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
