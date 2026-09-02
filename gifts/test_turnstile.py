from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.translation import gettext as _

from .models import User

TURNSTILE_SETTINGS = {
    "TURNSTILE_ENABLED": True,
    "TURNSTILE_SITE_KEY": "test-site-key",
    "TURNSTILE_SECRET_KEY": "test-secret-key",
    "TURNSTILE_VERIFY_URL": "https://challenges.cloudflare.com/turnstile/v0/siteverify",
    "TURNSTILE_TIMEOUT": 5,
}


@override_settings(**TURNSTILE_SETTINGS)
class TurnstileRegistrationTest(TestCase):
    registration_data = {
        "email": "alice@example.com",
        "password1": "a-secure-test-password-2026",
        "password2": "a-secure-test-password-2026",
    }

    def test_registration_page_embeds_turnstile(self):
        response = self.client.get(reverse("register"))

        self.assertContains(response, "test-site-key")
        self.assertContains(response, 'data-action="register"')
        self.assertContains(response, "challenges.cloudflare.com/turnstile/v0/api.js")

    def test_registration_requires_a_turnstile_token(self):
        response = self.client.post(reverse("register"), self.registration_data)

        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], None, _("Human verification failed. Please try again."))
        self.assertFalse(User.objects.filter(email="alice@example.com").exists())

    @patch("gifts.turnstile.requests.post")
    @patch("gifts.account.send_verification_email")
    def test_registration_accepts_a_valid_token(self, send_verification_email, post):
        siteverify_response = Mock()
        siteverify_response.raise_for_status.return_value = None
        siteverify_response.json.return_value = {"success": True, "action": "register"}
        post.return_value = siteverify_response

        data = {**self.registration_data, "cf-turnstile-response": "valid-token"}
        response = self.client.post(reverse("register"), data, REMOTE_ADDR="203.0.113.10")

        self.assertRedirects(response, reverse("verify_email_sent"), fetch_redirect_response=False)
        user = User.objects.get(email="alice@example.com")
        send_verification_email.assert_called_once()
        self.assertEqual(send_verification_email.call_args.args[1], user)
        post.assert_called_once_with(
            TURNSTILE_SETTINGS["TURNSTILE_VERIFY_URL"],
            data={
                "secret": "test-secret-key",
                "response": "valid-token",
                "remoteip": "203.0.113.10",
            },
            timeout=5,
        )

    @patch("gifts.turnstile.requests.post")
    def test_registration_rejects_wrong_action(self, post):
        siteverify_response = Mock()
        siteverify_response.raise_for_status.return_value = None
        siteverify_response.json.return_value = {"success": True, "action": "login"}
        post.return_value = siteverify_response

        data = {**self.registration_data, "cf-turnstile-response": "wrong-action-token"}
        response = self.client.post(reverse("register"), data)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="alice@example.com").exists())
