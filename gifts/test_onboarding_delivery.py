from datetime import timedelta
from smtplib import SMTPException
from unittest.mock import patch

from anymail.exceptions import AnymailError
from django.contrib.auth.tokens import default_token_generator
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.translation import override

from gifts.models import Group, OnboardingDailyCount, User
from gifts.onboarding import PENDING_GROUP_INVITE_SESSION_KEY, group_invitation_pending_value
from gifts.onboarding_metrics import record_onboarding_event
from gifts.tests import create_users


@override_settings(TURNSTILE_ENABLED=False)
class OnboardingDeliveryTest(TestCase):
    def setUp(self):
        self.owner, self.user, _ = create_users()
        self.group = Group.objects.create(name="Family", created_by=self.owner)
        self.group.members.add(self.owner)
        self.invitation_url = reverse("group_invitation", args=[self.group.invitation_token])

    def login(self):
        self.user.set_password("password")
        self.user.save(update_fields=["password"])
        return self.client.post(reverse("login"), {"username": self.user.email, "password": "password"})

    def test_existing_account_resumes_invitation_after_login_and_on_another_device(self):
        self.client.get(self.invitation_url)
        self.assertRedirects(self.login(), self.invitation_url)
        self.user.refresh_from_db()
        self.assertEqual(
            self.user.pending_group_invite_token, group_invitation_pending_value(self.group.invitation_token)
        )
        self.client.logout()
        self.assertRedirects(self.login(), self.invitation_url)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)

    def test_pending_invitation_does_not_trap_group_choice_and_skip_clears_it(self):
        self.user.onboarding_version = 0
        self.user.onboarding_completed_at = None
        self.user.save(update_fields=["onboarding_version", "onboarding_completed_at"])
        self.client.force_login(self.user)
        self.client.get(self.invitation_url)
        self.assertEqual(self.client.get(reverse("onboarding_group")).status_code, 200)
        self.assertRedirects(self.client.post(reverse("onboarding_group_skip")), reverse("dashboard"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.pending_group_invite_token, "")
        self.assertNotIn(PENDING_GROUP_INVITE_SESSION_KEY, self.client.session)
        self.client.post(reverse("onboarding_group_skip"))
        self.assertEqual(OnboardingDailyCount.objects.get(event="group_skipped").count, 1)

    def test_revoked_pending_link_is_cleared_for_anonymous_visitor(self):
        self.client.get(self.invitation_url)
        self.group.invitation_token = "new-private-token"
        self.group.save(update_fields=["invitation_token"])
        self.assertEqual(self.client.get(self.invitation_url).status_code, 404)
        self.assertNotIn(PENDING_GROUP_INVITE_SESSION_KEY, self.client.session)

    def test_email_opened_on_another_device_does_not_log_in_or_mix_identities(self):
        self.user.is_verified = False
        self.user.pending_group_invite_token = group_invitation_pending_value(self.group.invitation_token)
        self.user.save(update_fields=["is_verified", "pending_group_invite_token"])
        url = reverse(
            "verify_email_confirm",
            args=[urlsafe_base64_encode(force_bytes(self.user.pk)), default_token_generator.make_token(self.user)],
        )
        self.assertRedirects(self.client.get(url), reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)
        self.client.force_login(self.owner)
        self.assertRedirects(self.client.get(url), reverse("login"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.owner.pk)
        self.assertEqual(OnboardingDailyCount.objects.get(event="verified").count, 1)

    def test_invalid_verification_offers_recovery_in_both_languages(self):
        self.user.is_verified = False
        self.user.save(update_fields=["is_verified"])
        self.client.force_login(self.user)
        for language in ["fr", "en"]:
            with override(language):
                response = self.client.get(reverse("verify_email_confirm", args=["invalid", "invalid"]))
                self.assertContains(response, reverse("resend_verification"), status_code=400)

    def test_registration_never_echoes_password_when_validation_fails(self):
        response = self.client.post(
            reverse("register"), {"email": "invalid", "password1": "private-secret", "password2": "other"}
        )
        self.assertNotContains(response, 'value="private-secret"')

    def test_mail_failure_leaves_account_resumable(self):
        with patch("gifts.account.send_mail", side_effect=SMTPException("backend unavailable")):
            response = self.client.post(
                reverse("register"),
                {"email": "new@example.com", "password1": "strong-password-1234", "password2": "strong-password-1234"},
            )
        self.assertRedirects(response, reverse("verify_email_sent"))
        user = User.objects.get(email="new@example.com")
        self.assertFalse(user.is_verified)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_provider_failure_can_be_retried_without_leaking_its_error(self):
        self.user.is_verified = False
        self.user.save(update_fields=["is_verified"])
        self.client.force_login(self.user)
        with patch("gifts.account.send_mail", side_effect=AnymailError("private-provider-payload")):
            response = self.client.post(reverse("resend_verification"), follow=True)
        self.assertRedirects(response, reverse("verify_email_sent"))
        self.assertNotContains(response, "private-provider-payload")

    def test_cleanup_preserves_existing_accounts_reverifying_their_email(self):
        self.user.is_verified = False
        self.user.date_joined = timezone.now() - timedelta(days=365)
        self.user.save(update_fields=["is_verified", "date_joined"])
        expired = User.objects.create_user(
            email="expired@example.com",
            username="expired@example.com",
            date_joined=timezone.now() - timedelta(minutes=31),
            is_verified=False,
        )
        call_command("cleanup_unverified_users")
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        self.assertFalse(User.objects.filter(pk=expired.pk).exists())

    def test_counters_store_only_daily_totals_and_reject_arbitrary_data(self):
        record_onboarding_event("registered", self.user)
        record_onboarding_event("registered", self.user)
        counter = OnboardingDailyCount.objects.get()
        self.assertEqual(counter.count, 2)
        self.assertEqual(counter.day, timezone.localdate())
        self.assertEqual({field.name for field in counter._meta.fields}, {"id", "day", "event", "count"})
        with self.assertRaises(ValueError):
            record_onboarding_event(self.user.email, self.user)

    def test_demo_account_is_excluded_from_counters(self):
        self.user.is_demo = True
        self.user.save(update_fields=["is_demo"])
        record_onboarding_event("registered", self.user)
        self.assertFalse(OnboardingDailyCount.objects.exists())

    def test_unverified_user_cannot_add_a_managed_person(self):
        self.owner.is_verified = False
        self.owner.save(update_fields=["is_verified"])
        self.client.force_login(self.owner)
        response = self.client.post(reverse("add_managed_member", args=[self.group.pk]), {"name": "Child"})
        self.assertRedirects(response, reverse("verify_email_sent"))
        self.assertFalse(self.group.managed_members.exists())


class OnboardingDeliveryMigrationTest(TransactionTestCase):
    before = [("gifts", "0038_shared_gift_publications")]
    after = [("gifts", "0044_repair_managed_members")]

    def setUp(self):
        self.addCleanup(self.restore_latest)
        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        self.old_apps = executor.loader.project_state(self.before).apps

    def restore_latest(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def test_pre_onboarding_data_is_preserved_and_managed_people_are_repaired(self):
        User = self.old_apps.get_model("gifts", "User")
        Group = self.old_apps.get_model("gifts", "Group")
        member_model = self.old_apps.get_model("gifts", "ManagedMember")
        Gift = self.old_apps.get_model("gifts", "Gift")
        owner = User.objects.create(
            email="old@example.com", username="old@example.com", nickname="Old", is_verified=True
        )
        waiting = User.objects.create(email="waiting@example.com", username="waiting@example.com", is_verified=False)
        person = User.objects.create(
            email="managed@example.com",
            username="managed@example.com",
            nickname="Child",
            is_managed=True,
            is_active=False,
        )
        group = Group.objects.create(name="Family", created_by=owner, group_token="OLDCODE")
        group.members.add(owner, person)
        old_gift = Gift.objects.create(owner=person, created_by=owner, title="Bike")
        legacy = member_model.objects.create(group=group, name="Relative", color="")
        legacy_gift = Gift.objects.create(owner=owner, created_by=owner, managed_member=legacy, title="Book")

        apps = self.migrate(self.after)
        User = apps.get_model("gifts", "User")
        member_model = apps.get_model("gifts", "ManagedMember")
        Gift = apps.get_model("gifts", "Gift")
        self.assertEqual(User.objects.get(pk=owner.pk).onboarding_version, 1)
        self.assertIsNotNone(User.objects.get(pk=owner.pk).profile_completed_at)
        self.assertEqual(User.objects.get(pk=waiting.pk).onboarding_version, 0)
        self.assertEqual(member_model.objects.get(user_id=person.pk).name, "Child")
        self.assertTrue(User.objects.get(pk=person.pk).password.startswith("!"))
        repaired = member_model.objects.get(pk=legacy.pk)
        self.assertIsNotNone(repaired.user_id)
        self.assertFalse(User.objects.get(pk=repaired.user_id).is_active)
        self.assertTrue(repaired.color)
        self.assertEqual(Gift.objects.get(pk=old_gift.pk).owner_id, person.pk)
        self.assertEqual(Gift.objects.get(pk=legacy_gift.pk).owner_id, repaired.user_id)
        group = apps.get_model("gifts", "Group").objects.get(pk=group.pk)
        self.assertEqual(group.group_token, "OLDCODE")
        self.assertGreaterEqual(len(group.invitation_token), 40)
        self.assertEqual(group.members.count(), 3)

        # A schema rollback is possible in a disposable rehearsal, and repair is idempotent.
        self.migrate(self.before)
        apps = self.migrate(self.after)
        self.assertEqual(apps.get_model("gifts", "ManagedMember").objects.count(), 2)
        self.assertEqual(apps.get_model("gifts", "Gift").objects.count(), 2)

    def test_every_migration_from_0038_to_head_reverses_cleanly(self):
        executor = MigrationExecutor(connection)
        head = executor.loader.graph.leaf_nodes()

        self.migrate(head)
        self.migrate(self.before)  # a full rollback to the pre-onboarding schema
        restored = self.migrate(head)

        # The onboarding invariant is back in place after the round trip.
        constraint_names = {c.name for c in restored.get_model("gifts", "User")._meta.constraints}
        self.assertIn("onboarding_completed_iff_versioned", constraint_names)

    def test_ambiguous_managed_identity_stops_migration_without_guessing(self):
        User = self.old_apps.get_model("gifts", "User")
        orphan = User.objects.create(
            email="orphan@example.com", username="orphan@example.com", is_managed=True, is_active=False
        )
        with self.assertRaisesMessage(RuntimeError, "manual review"):
            self.migrate(self.after)
        self.assertFalse(self.old_apps.get_model("gifts", "ManagedMember").objects.exists())
        # Resolve the test fixture so cleanup can restore the latest schema.
        User.objects.filter(pk=orphan.pk).delete()
