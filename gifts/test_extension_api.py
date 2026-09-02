import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Gift, Group, User
from .onboarding import CURRENT_ONBOARDING_VERSION


def pkce_challenge(verifier):
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class ExtensionApiTest(TestCase):
    redirect_uri = "https://test.extensions.allizom.org/"
    verifier = "a" * 64
    state = "test-state-123456"

    def setUp(self):
        self.user = User.objects.create_user(
            username="quick@example.com",
            email="quick@example.com",
            nickname="Quick",
            is_verified=True,
            onboarding_version=CURRENT_ONBOARDING_VERSION,
            profile_completed_at=timezone.now(),
        )

    def authorize(self, verifier=None):
        verifier = verifier or self.verifier
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("extension_authorize"),
            {
                "redirect_uri": self.redirect_uri,
                "state": self.state,
                "code_challenge": pkce_challenge(verifier),
            },
        )
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response["Location"]).query)
        self.assertEqual(query["state"], [self.state])
        return query["code"][0]

    def exchange(self, code, verifier=None):
        return self.client.post(
            reverse("extension_token"),
            data={
                "code": code,
                "code_verifier": verifier or self.verifier,
                "redirect_uri": self.redirect_uri,
            },
            content_type="application/json",
        )

    def access_token(self):
        response = self.exchange(self.authorize())
        self.assertEqual(response.status_code, 200)
        return response.json()["access_token"]

    def auth(self, token):
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def test_authorization_requires_login(self):
        response = self.client.get(
            reverse("extension_authorize"),
            {
                "redirect_uri": self.redirect_uri,
                "state": self.state,
                "code_challenge": pkce_challenge(self.verifier),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_authorization_rejects_arbitrary_redirect(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("extension_authorize"),
            {
                "redirect_uri": "https://attacker.example/callback",
                "state": self.state,
                "code_challenge": pkce_challenge(self.verifier),
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_code_exchange_is_pkce_protected_and_one_time(self):
        code = self.authorize()
        invalid = self.exchange(code, verifier="b" * 64)
        self.assertEqual(invalid.status_code, 401)

        valid = self.exchange(code)
        self.assertEqual(valid.status_code, 200)
        self.assertTrue(valid.json()["access_token"].startswith("nce_"))

        replay = self.exchange(code)
        self.assertEqual(replay.status_code, 401)

    def test_me_and_revoke(self):
        token = self.access_token()
        response = self.client.get(reverse("extension_me"), **self.auth(token))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["nickname"], self.user.nickname)
        self.assertNotIn("email", response.json()["user"])

        response = self.client.post(
            reverse("extension_revoke"), data={}, content_type="application/json", **self.auth(token)
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.client.get(reverse("extension_me"), **self.auth(token)).status_code, 401)

    def test_quick_add_creates_product_with_extracted_fields(self):
        token = self.access_token()
        group = Group.objects.create(name="Family", created_by=self.user)
        group.members.add(self.user)
        payload = {
            "title": "Leather backpack",
            "url": "https://shop.example/products/backpack",
            "image_url": "https://shop.example/images/backpack.jpg",
            "price": "129.90",
            "currency": "eur",
            "visible_in": [group.id],
        }

        response = self.client.post(
            reverse("extension_quick_add"), data=payload, content_type="application/json", **self.auth(token)
        )

        self.assertEqual(response.status_code, 201)
        gift = Gift.objects.get()
        self.assertEqual(gift.owner, self.user)
        self.assertEqual(gift.title, payload["title"])
        self.assertEqual(gift.image_url, payload["image_url"])
        self.assertEqual(str(gift.price), "129.90")
        self.assertEqual(gift.currency, "EUR")
        self.assertEqual(list(gift.visible_in.all()), [group])

        duplicate = self.client.post(
            reverse("extension_quick_add"), data=payload, content_type="application/json", **self.auth(token)
        )
        self.assertEqual(duplicate.status_code, 409)

    def test_quick_add_rejects_another_users_group(self):
        token = self.access_token()
        other = User.objects.create_user(
            username="other@example.com",
            email="other@example.com",
            nickname="Other",
            is_verified=True,
            onboarding_version=CURRENT_ONBOARDING_VERSION,
            profile_completed_at=timezone.now(),
        )
        group = Group.objects.create(name="Other group", created_by=other)
        group.members.add(other)
        response = self.client.post(
            reverse("extension_quick_add"),
            data={
                "title": "Product",
                "url": "https://shop.example/product",
                "image_url": "",
                "price": "10",
                "currency": "EUR",
                "visible_in": [group.id],
            },
            content_type="application/json",
            **self.auth(token),
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Gift.objects.exists())
