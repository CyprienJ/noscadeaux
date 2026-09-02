from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.translation import gettext as _

from .forms import LocalUserCreationForm
from .models import Group, User
from .onboarding import (
    CURRENT_ONBOARDING_VERSION,
    PENDING_GROUP_INVITE_SESSION_KEY,
    get_onboarding_next_url,
    onboarding_is_complete,
)


@override_settings(TURNSTILE_ENABLED=False)
class RegistrationOnboardingTest(TestCase):
    registration_data = {
        "email": "alice@example.com",
        "password1": "a-secure-test-password-2026",
        "password2": "a-secure-test-password-2026",
    }

    def test_registration_only_asks_for_credentials(self):
        response = self.client.get(reverse("register"))

        self.assertEqual(list(response.context["form"].fields), ["email", "password1", "password2"])

    def test_registration_escapes_untrusted_help_text(self):
        form = LocalUserCreationForm()
        form.fields["email"].help_text = '<img src=x onerror="alert(1)">'
        with patch("gifts.account.LocalUserCreationForm", return_value=form):
            response = self.client.get(reverse("register"))

        self.assertNotContains(response, form.fields["email"].help_text)
        self.assertContains(response, "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;")

    def test_registration_preserves_password_help_list(self):
        response = self.client.get(reverse("register"))
        help_text = str(response.context["form"].fields["password1"].help_text)

        self.assertIn("<ul>", help_text)
        self.assertContains(
            response,
            f'<div class="form-text small opacity-75" id="id_password1_helptext">{help_text}</div>',
            html=True,
        )

    def test_registration_creates_an_incomplete_user_and_opens_verification_page(self):
        response = self.client.post(reverse("register"), self.registration_data)

        self.assertRedirects(response, reverse("verify_email_sent"))
        user = User.objects.get(email="alice@example.com")
        self.assertEqual(user.nickname, "")
        self.assertFalse(user.is_verified)
        self.assertEqual(user.onboarding_version, 0)
        self.assertIsNone(user.onboarding_completed_at)
        self.assertIsNone(user.profile_completed_at)
        self.assertIsNotNone(user.verification_email_sent_at)
        self.assertEqual(len(mail.outbox), 1)

    def test_registration_normalizes_email_and_rejects_case_insensitive_duplicate(self):
        User.objects.create_user(
            email="alice@example.com",
            username="alice@example.com",
            password="password",
            nickname="Alice",
        )
        data = {**self.registration_data, "email": "  ALICE@EXAMPLE.COM "}

        response = self.client.post(reverse("register"), data)

        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "email", _("A user with that email already exists."))
        self.assertEqual(User.objects.filter(email__iexact="alice@example.com").count(), 1)

    def test_verification_page_displays_recipient_email(self):
        self.client.post(reverse("register"), self.registration_data)

        response = self.client.get(reverse("verify_email_sent"))

        self.assertContains(response, "alice@example.com")

    def test_valid_verification_link_marks_user_verified(self):
        self.client.post(reverse("register"), self.registration_data)
        user = User.objects.get(email="alice@example.com")
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)

        response = self.client.get(reverse("verify_email_confirm", kwargs={"uidb64": uid, "token": token}))

        self.assertRedirects(response, reverse("onboarding_profile"), fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertTrue(user.is_verified)
        self.assertEqual(user.onboarding_version, 0)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    VERIFICATION_EMAIL_RESEND_COOLDOWN_SECONDS=60,
)
class VerificationEmailCooldownTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="waiting@example.com",
            username="waiting@example.com",
            password="password",
            nickname="",
            is_verified=False,
        )
        self.client.force_login(self.user)

    def test_recent_email_cannot_be_resent(self):
        self.user.verification_email_sent_at = timezone.now()
        self.user.save(update_fields=["verification_email_sent_at"])

        response = self.client.post(reverse("resend_verification"))

        self.assertRedirects(response, reverse("verify_email_sent"))
        self.assertEqual(len(mail.outbox), 0)

    def test_email_can_be_resent_after_cooldown(self):
        previous_sent_at = timezone.now() - timedelta(seconds=61)
        self.user.verification_email_sent_at = previous_sent_at
        self.user.save(update_fields=["verification_email_sent_at"])

        response = self.client.post(reverse("resend_verification"))

        self.assertRedirects(response, reverse("verify_email_sent"))
        self.assertEqual(len(mail.outbox), 1)
        self.user.refresh_from_db()
        self.assertGreater(self.user.verification_email_sent_at, previous_sent_at)

    def test_resend_requires_post(self):
        response = self.client.get(reverse("resend_verification"))

        self.assertEqual(response.status_code, 405)
        self.assertEqual(len(mail.outbox), 0)

    def test_verified_user_does_not_receive_another_email(self):
        self.user.is_verified = True
        self.user.save(update_fields=["is_verified"])

        response = self.client.post(reverse("resend_verification"))

        self.assertRedirects(response, reverse("onboarding_profile"))
        self.assertEqual(len(mail.outbox), 0)


@override_settings(
    TURNSTILE_ENABLED=False,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    AUTH_RATE_WINDOW_SECONDS=900,
    REGISTRATION_MAX_ATTEMPTS_PER_IP_WINDOW=3,
    VERIFICATION_RESEND_MAX_ATTEMPTS_PER_IP_WINDOW=2,
    VERIFICATION_EMAIL_RESEND_COOLDOWN_SECONDS=0,
)
class AuthPerIpRateLimitTest(TestCase):
    def test_registration_is_blocked_per_ip_past_the_threshold(self):
        for index in range(3):
            response = self.client.post(
                reverse("register"),
                {
                    "email": f"user{index}@example.com",
                    "password1": "a-secure-test-password-2026",
                    "password2": "a-secure-test-password-2026",
                },
            )
            self.assertRedirects(response, reverse("verify_email_sent"))
            self.client.logout()

        response = self.client.post(
            reverse("register"),
            {
                "email": "blocked@example.com",
                "password1": "a-secure-test-password-2026",
                "password2": "a-secure-test-password-2026",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="blocked@example.com").exists())
        self.assertContains(response, _("Too many attempts from your network. Please try again later."))

    def test_resend_verification_is_blocked_per_ip_past_the_threshold(self):
        user = User.objects.create_user(
            email="waiting@example.com",
            username="waiting@example.com",
            password="password",
            nickname="",
            is_verified=False,
        )
        self.client.force_login(user)

        for _index in range(2):
            self.client.post(reverse("resend_verification"))
        mail.outbox.clear()

        response = self.client.post(reverse("resend_verification"))

        self.assertRedirects(response, reverse("verify_email_sent"))
        self.assertEqual(len(mail.outbox), 0)

    def test_registration_is_allowed_again_once_the_window_has_passed(self):
        from gifts.models import AuthThrottleEvent

        for index in range(3):
            self.client.post(
                reverse("register"),
                {
                    "email": f"early{index}@example.com",
                    "password1": "a-secure-test-password-2026",
                    "password2": "a-secure-test-password-2026",
                },
            )
            self.client.logout()
        blocked = self.client.post(
            reverse("register"),
            {
                "email": "blocked@example.com",
                "password1": "a-secure-test-password-2026",
                "password2": "a-secure-test-password-2026",
            },
        )
        self.assertEqual(blocked.status_code, 200)

        AuthThrottleEvent.objects.update(created_at=timezone.now() - timedelta(seconds=901))
        self.client.logout()
        allowed = self.client.post(
            reverse("register"),
            {
                "email": "later@example.com",
                "password1": "a-secure-test-password-2026",
                "password2": "a-secure-test-password-2026",
            },
        )
        self.assertRedirects(allowed, reverse("verify_email_sent"))


class AuthThrottleCleanupTest(TestCase):
    @override_settings(AUTH_RATE_WINDOW_SECONDS=900)
    def test_cleanup_prunes_only_events_older_than_the_window(self):
        from django.core.management import call_command

        from gifts.models import AuthThrottleEvent

        recent = AuthThrottleEvent.objects.create(ip_hash="a" * 64, action=AuthThrottleEvent.ACTION_REGISTER)
        stale = AuthThrottleEvent.objects.create(ip_hash="b" * 64, action=AuthThrottleEvent.ACTION_REGISTER)
        AuthThrottleEvent.objects.filter(pk=stale.pk).update(created_at=timezone.now() - timedelta(seconds=901))

        call_command("cleanup_auth_throttle_events")

        self.assertEqual(list(AuthThrottleEvent.objects.values_list("pk", flat=True)), [recent.pk])


class AccountStateInvariantTest(TestCase):
    def _fresh_user(self):
        return User.objects.create_user(
            email="state@example.com",
            username="state@example.com",
            password="password",
            nickname="",
        )

    def test_database_rejects_a_version_without_a_completion_time(self):
        from django.db import IntegrityError

        user = self._fresh_user()
        with self.assertRaises(IntegrityError):
            # .update() bypasses User.save() normalisation, exercising the constraint.
            User.objects.filter(pk=user.pk).update(onboarding_version=1)

    def test_database_rejects_a_completion_time_without_a_version(self):
        from django.db import IntegrityError

        user = self._fresh_user()
        with self.assertRaises(IntegrityError):
            User.objects.filter(pk=user.pk).update(onboarding_completed_at=timezone.now())

    def test_reaching_a_version_stamps_a_completion_time(self):
        user = self._fresh_user()
        user.onboarding_version = 1
        user.save(update_fields=["onboarding_version"])
        user.refresh_from_db()
        self.assertIsNotNone(user.onboarding_completed_at)


class OnboardingResolverTest(TestCase):
    def test_anonymous_user_goes_to_welcome(self):
        self.assertEqual(get_onboarding_next_url(AnonymousUser()), reverse("welcome"))

    def test_unverified_user_goes_to_email_verification(self):
        user = User(is_verified=False, onboarding_version=0)

        self.assertEqual(get_onboarding_next_url(user), reverse("verify_email_sent"))

    def test_verified_user_without_profile_goes_to_profile_setup(self):
        user = User(is_verified=True, onboarding_version=0, profile_completed_at=None)

        self.assertFalse(onboarding_is_complete(user))
        self.assertEqual(get_onboarding_next_url(user), reverse("onboarding_profile"))

    def test_user_with_profile_goes_to_group_choice(self):
        user = User(
            is_verified=True,
            onboarding_version=0,
            profile_completed_at=timezone.now(),
        )

        self.assertFalse(onboarding_is_complete(user))
        self.assertEqual(get_onboarding_next_url(user), reverse("onboarding_group"))

    def test_pending_invitation_takes_priority_over_group_choice(self):
        user = User(
            is_verified=True,
            onboarding_version=0,
            profile_completed_at=timezone.now(),
            pending_group_invite_token="ABC123",
        )

        self.assertEqual(
            get_onboarding_next_url(user),
            reverse("join_group", kwargs={"token": "ABC123"}),
        )

    def test_existing_user_at_current_version_is_complete(self):
        user = User(
            is_verified=True,
            onboarding_version=CURRENT_ONBOARDING_VERSION,
            profile_completed_at=timezone.now(),
        )

        self.assertTrue(onboarding_is_complete(user))
        self.assertEqual(get_onboarding_next_url(user), reverse("dashboard"))

    def test_adding_a_step_is_a_single_list_entry(self):
        from unittest.mock import patch

        from .onboarding import ONBOARDING_STEPS, OnboardingStep, next_onboarding_step

        extra = OnboardingStep(
            name="newsletter",
            url_name="dashboard",
            is_complete=lambda user: False,
            allowed_url_names=frozenset(),
        )
        done_user = User(
            is_verified=True,
            onboarding_version=CURRENT_ONBOARDING_VERSION,
            profile_completed_at=timezone.now(),
        )
        self.assertIsNone(next_onboarding_step(done_user))

        with patch("gifts.onboarding.ONBOARDING_STEPS", (*ONBOARDING_STEPS, extra)):
            self.assertEqual(next_onboarding_step(done_user).name, "newsletter")

    def test_onboarding_stage_names_the_current_step(self):
        from .onboarding import onboarding_stage

        now = timezone.now()
        self.assertEqual(onboarding_stage(User(is_verified=False, onboarding_version=0)), "verify_email")
        self.assertEqual(
            onboarding_stage(User(is_verified=True, onboarding_version=0, profile_completed_at=None)), "profile"
        )
        self.assertEqual(
            onboarding_stage(User(is_verified=True, onboarding_version=0, profile_completed_at=now)), "group"
        )
        self.assertEqual(
            onboarding_stage(
                User(is_verified=True, onboarding_version=CURRENT_ONBOARDING_VERSION, profile_completed_at=now)
            ),
            "done",
        )


class PendingInvitePrecedenceTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@example.com",
            username="owner@example.com",
            password="password",
            nickname="Owner",
            is_verified=True,
            profile_completed_at=timezone.now(),
            onboarding_version=CURRENT_ONBOARDING_VERSION,
        )
        self.session_group = Group.objects.create(name="Session group", created_by=self.owner)
        self.session_group.members.add(self.owner)
        self.field_group = Group.objects.create(name="Field group", created_by=self.owner)
        self.field_group.members.add(self.owner)

    def test_session_pending_token_wins_over_the_persisted_one(self):
        # Verified with a profile but not through the group step yet, so the
        # pending invitation is what get_onboarding_next_url resolves.
        user = User.objects.create_user(
            email="joiner@example.com",
            username="joiner@example.com",
            password="password",
            nickname="Joiner",
            is_verified=True,
            profile_completed_at=timezone.now(),
            pending_group_invite_token=self.field_group.group_token,
        )
        self.client.force_login(user)
        session = self.client.session
        session[PENDING_GROUP_INVITE_SESSION_KEY] = self.session_group.group_token
        session.save()

        response = self.client.get(reverse("dashboard"))

        self.assertRedirects(
            response,
            reverse("join_group", kwargs={"token": self.session_group.group_token}),
            fetch_redirect_response=False,
        )


class OnboardingProfileTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="profile@example.com",
            username="profile@example.com",
            password="password",
            nickname="",
            is_verified=True,
        )
        self.client.force_login(self.user)

    def test_incomplete_user_is_redirected_from_dashboard_to_profile(self):
        response = self.client.get(reverse("dashboard"))

        self.assertRedirects(response, reverse("onboarding_profile"))

    def test_profile_page_explains_each_piece_of_information(self):
        response = self.client.get(reverse("onboarding_profile"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, _("This name is visible to people in your groups."))
        self.assertContains(response, _("We do not ask for the year."), html=False)
        self.assertContains(response, _("Optional — it helps members of your groups recognize you."))

    def test_profile_requires_a_nickname(self):
        response = self.client.post(reverse("onboarding_profile"), {"nickname": ""})

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.profile_completed_at)

    def test_profile_rejects_partial_or_impossible_birthday(self):
        partial = self.client.post(
            reverse("onboarding_profile"),
            {"nickname": "Alice", "birthday_month": "2", "birthday_day": ""},
        )
        impossible = self.client.post(
            reverse("onboarding_profile"),
            {"nickname": "Alice", "birthday_month": "2", "birthday_day": "30"},
        )

        self.assertEqual(partial.status_code, 200)
        self.assertEqual(impossible.status_code, 200)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.profile_completed_at)

    def test_profile_can_be_completed_without_birthday_or_photo(self):
        response = self.client.post(reverse("onboarding_profile"), {"nickname": "  Alice  "})

        self.assertRedirects(response, reverse("onboarding_group"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.nickname, "Alice")
        self.assertIsNone(self.user.birthday)
        self.assertFalse(self.user.avatar)
        self.assertIsNotNone(self.user.profile_completed_at)
        self.assertEqual(self.user.onboarding_version, 0)

    def test_profile_saves_birthday_without_year(self):
        response = self.client.post(
            reverse("onboarding_profile"),
            {"nickname": "Alice", "birthday_month": "12", "birthday_day": "24"},
        )

        self.assertRedirects(response, reverse("onboarding_group"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.birthday_month, 12)
        self.assertEqual(self.user.birthday_day, 24)
        self.assertFalse(hasattr(self.user, "birthday_year"))

    def test_completed_profile_cannot_reenter_setup(self):
        self.user.nickname = "Alice"
        self.user.profile_completed_at = timezone.now()
        self.user.save(update_fields=["nickname", "profile_completed_at"])

        response = self.client.get(reverse("onboarding_profile"))

        self.assertRedirects(response, reverse("onboarding_group"))

    def test_photo_editor_returns_to_onboarding_without_completing_profile(self):
        response = self.client.get(reverse("photo_upload_profile"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["back_url"], reverse("onboarding_profile"))
        self.assertTrue(response.context["is_onboarding"])
        self.user.refresh_from_db()
        self.assertIsNone(self.user.profile_completed_at)

    def test_unverified_user_cannot_open_profile_setup(self):
        self.user.is_verified = False
        self.user.save(update_fields=["is_verified"])

        response = self.client.get(reverse("onboarding_profile"))

        self.assertRedirects(response, reverse("verify_email_sent"))


class GroupOnboardingTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="new@example.com",
            username="new@example.com",
            password="password",
            nickname="New member",
            is_verified=True,
            profile_completed_at=timezone.now(),
        )
        self.owner = User.objects.create_user(
            email="owner@example.com",
            username="owner@example.com",
            password="password",
            nickname="SecretOwner",
            is_verified=True,
            profile_completed_at=timezone.now(),
            onboarding_version=CURRENT_ONBOARDING_VERSION,
            onboarding_completed_at=timezone.now(),
        )
        self.group = Group.objects.create(name="Family", created_by=self.owner)
        self.group.members.add(self.owner)
        self.client.force_login(self.user)

    def test_choice_page_offers_create_join_and_later(self):
        response = self.client.get(reverse("onboarding_group"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, _("Create a group"))
        self.assertContains(response, _("Join with a code"))
        self.assertContains(response, _("I'll do this later"))

    def test_skip_is_post_only_and_completes_onboarding(self):
        self.assertEqual(self.client.get(reverse("onboarding_group_skip")).status_code, 405)

        response = self.client.post(reverse("onboarding_group_skip"))

        self.assertRedirects(response, reverse("dashboard"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.onboarding_version, CURRENT_ONBOARDING_VERSION)
        self.assertIsNotNone(self.user.onboarding_completed_at)

    def test_create_group_adds_creator_and_completes_onboarding(self):
        response = self.client.post(reverse("create_group"), {"name": "Friends"})

        created_group = Group.objects.get(name="Friends")
        self.assertRedirects(response, reverse("group_invitations", args=[created_group.id]))
        self.assertTrue(created_group.members.filter(pk=self.user.pk).exists())
        self.user.refresh_from_db()
        self.assertTrue(onboarding_is_complete(self.user))

    def test_valid_code_opens_preview_then_post_joins_group(self):
        response = self.client.post(reverse("onboarding_join_group"), {"code": self.group.group_token.lower()})

        preview_url = reverse("join_group", kwargs={"token": self.group.group_token})
        self.assertRedirects(response, preview_url, fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertFalse(onboarding_is_complete(self.user))
        self.assertEqual(self.user.pending_group_invite_token, self.group.group_token)

        preview = self.client.get(preview_url)
        self.assertEqual(preview.status_code, 200)

        confirm_url = reverse("join_group_confirm", kwargs={"token": self.group.group_token})
        self.assertEqual(self.client.get(confirm_url).status_code, 405)
        accepted = self.client.post(confirm_url)

        self.assertRedirects(accepted, reverse("group_detail", args=[self.group.id]))
        self.assertTrue(self.group.members.filter(pk=self.user.pk).exists())
        self.user.refresh_from_db()
        self.assertTrue(onboarding_is_complete(self.user))
        self.assertEqual(self.user.pending_group_invite_token, "")

    def test_accepting_twice_is_idempotent(self):
        confirm_url = reverse("join_group_confirm", kwargs={"token": self.group.group_token})

        self.client.post(confirm_url)
        self.client.post(confirm_url)

        self.assertEqual(self.group.members.filter(pk=self.user.pk).count(), 1)

    def test_acceptance_requires_a_csrf_token(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)

        response = csrf_client.post(reverse("join_group_confirm", kwargs={"token": self.group.group_token}))

        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.group.members.filter(pk=self.user.pk).exists())

    def test_preview_does_not_complete_an_existing_members_onboarding(self):
        self.group.members.add(self.user)

        response = self.client.get(reverse("join_group", kwargs={"token": self.group.group_token}))

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(onboarding_is_complete(self.user))
        self.assertContains(response, _("Continue to the group"))

    def test_invalid_code_keeps_onboarding_open(self):
        response = self.client.post(reverse("onboarding_join_group"), {"code": "UNKNOWN"}, follow=True)

        self.assertRedirects(response, reverse("onboarding_group"))
        self.assertContains(response, _("No group found with this code."))
        self.user.refresh_from_db()
        self.assertFalse(onboarding_is_complete(self.user))

    def test_invitation_that_becomes_invalid_returns_to_group_choice(self):
        self.user.pending_group_invite_token = "REMOVED"
        self.user.save(update_fields=["pending_group_invite_token"])
        session = self.client.session
        session[PENDING_GROUP_INVITE_SESSION_KEY] = "REMOVED"
        session.save()

        response = self.client.get(reverse("dashboard"), follow=True)

        self.assertRedirects(response, reverse("onboarding_group"))
        self.assertContains(response, _("This invitation is no longer valid. Choose another group."))
        self.user.refresh_from_db()
        self.assertEqual(self.user.pending_group_invite_token, "")


@override_settings(TURNSTILE_ENABLED=False)
class PendingInvitationRegistrationTest(TestCase):
    registration_data = {
        "email": "invited@example.com",
        "password1": "a-secure-test-password-2026",
        "password2": "a-secure-test-password-2026",
    }

    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@example.com",
            username="owner@example.com",
            password="password",
            nickname="PrivateNickname",
            is_verified=True,
            profile_completed_at=timezone.now(),
            onboarding_version=CURRENT_ONBOARDING_VERSION,
        )
        self.group = Group.objects.create(name="Invited group", created_by=self.owner)
        self.group.members.add(self.owner)

    def test_anonymous_preview_is_limited_and_remembers_invitation(self):
        response = self.client.get(reverse("join_group", kwargs={"token": self.group.group_token}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.group.name)
        self.assertContains(response, _("Create an account"))
        self.assertNotContains(response, self.owner.nickname)
        self.assertEqual(
            self.client.session[PENDING_GROUP_INVITE_SESSION_KEY],
            self.group.group_token,
        )

    def test_invitation_survives_registration_verification_and_profile(self):
        invite_url = reverse("join_group", kwargs={"token": self.group.group_token})
        self.client.get(invite_url)
        registration = self.client.post(
            f"{reverse('register')}?next=https://attacker.example/escape",
            self.registration_data,
        )

        self.assertRedirects(registration, reverse("verify_email_sent"))
        user = User.objects.get(email="invited@example.com")
        self.assertEqual(user.pending_group_invite_token, self.group.group_token)

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        verified = self.client.get(reverse("verify_email_confirm", kwargs={"uidb64": uid, "token": token}))
        self.assertRedirects(verified, reverse("onboarding_profile"), fetch_redirect_response=False)

        profile = self.client.post(reverse("onboarding_profile"), {"nickname": "Invited"})
        self.assertRedirects(profile, invite_url, fetch_redirect_response=False)

    def test_persisted_invitation_is_available_on_another_device(self):
        user = User.objects.create_user(
            email="cross-device@example.com",
            username="cross-device@example.com",
            password="password",
            nickname="Cross device",
            is_verified=True,
            profile_completed_at=timezone.now(),
            pending_group_invite_token=self.group.group_token,
        )
        other_client = self.client_class()
        other_client.force_login(user)

        response = other_client.get(reverse("dashboard"))

        self.assertRedirects(
            response,
            reverse("join_group", kwargs={"token": self.group.group_token}),
            fetch_redirect_response=False,
        )


class AccountSetupMiddlewareAllowlistTest(TestCase):
    """The middleware pins a mid-onboarding user to their step, but a few routes
    stay reachable regardless (admin for staff, event lists for everyone)."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="mid@example.com",
            username="mid@example.com",
            password="password",
            nickname="Mid",
            is_verified=True,
            profile_completed_at=timezone.now(),
        )

    def test_mid_onboarding_staff_user_can_still_reach_the_admin(self):
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=["is_staff", "is_superuser"])
        self.client.force_login(self.user)

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)

    def test_mid_onboarding_user_can_open_an_event_list(self):
        from .models import EventList

        event = EventList.objects.create(name="Party", owner=self.user)
        self.client.force_login(self.user)

        response = self.client.get(reverse("event_detail", kwargs={"token": event.access_token}))

        self.assertEqual(response.status_code, 200)


class OnboardingRedirectLoopSweepTest(TestCase):
    """Every project URL, for every onboarding state, must settle without a
    redirect cycle (lot 6, B1/B2)."""

    @staticmethod
    def _concrete_paths():
        import re

        from django.urls import get_resolver
        from django.urls.resolvers import URLPattern, URLResolver

        def walk(resolver, prefix):
            for entry in resolver.url_patterns:
                route = prefix + str(entry.pattern)
                if isinstance(entry, URLResolver):
                    yield from walk(entry, route)
                elif isinstance(entry, URLPattern):
                    yield route

        def fill(match):
            spec = match.group(1)
            if spec.startswith(("int:", "pk")) or spec == "int":
                return "1"
            if "uuid" in spec:
                return "00000000-0000-0000-0000-000000000000"
            return "sample"

        seen = set()
        for route in walk(get_resolver(), ""):
            if route.startswith(("media/", "static/")):
                continue
            path = "/" + re.sub(r"<([^>]+)>", fill, route)
            if "<" in path or path in seen:
                continue
            seen.add(path)
            yield path

    def _states(self):
        base = dict(password="password", nickname="Sweep")
        unverified = User.objects.create_user(
            email="s-unverified@example.com", username="s-unverified@example.com", is_verified=False, **base
        )
        no_profile = User.objects.create_user(
            email="s-noprofile@example.com", username="s-noprofile@example.com", is_verified=True, **base
        )
        no_group = User.objects.create_user(
            email="s-nogroup@example.com",
            username="s-nogroup@example.com",
            is_verified=True,
            profile_completed_at=timezone.now(),
            **base,
        )
        done = User.objects.create_user(
            email="s-done@example.com",
            username="s-done@example.com",
            is_verified=True,
            profile_completed_at=timezone.now(),
            onboarding_version=CURRENT_ONBOARDING_VERSION,
            onboarding_completed_at=timezone.now(),
            **base,
        )
        return [unverified, no_profile, no_group, done, None]

    def test_no_redirect_cycle_for_any_url_in_any_state(self):
        from django.test.client import RedirectCycleError

        paths = list(self._concrete_paths())
        self.assertGreater(len(paths), 20)
        for user in self._states():
            client = self.client_class()
            if user is not None:
                client.force_login(user)
            for path in paths:
                try:
                    client.get(path, follow=True)
                except RedirectCycleError as error:  # pragma: no cover - failure path
                    self.fail(f"redirect cycle at {path} for {user}: {error}")
