import io
import json
import os
import shutil
import tempfile
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.translation import activate, deactivate
from django.utils.translation import gettext as _
from PIL import Image

from .demo import DEMO_EMAIL
from .models import (
    BalanceSettlement,
    EventList,
    Gift,
    GiftComment,
    GiftTag,
    Group,
    GuestReservation,
    ManagedMember,
    NotificationDigestPreference,
    Reservation,
    SecretSantaAssignment,
    SecretSantaExclusion,
    SecretSantaGuestParticipant,
    SharedGiftPublication,
    SharedList,
    Subscription,
    User,
)
from .onboarding import CURRENT_ONBOARDING_VERSION
from .views import compute_group_balances


def make_image(name="test.jpg", width=200, height=200):
    img = Image.new("RGB", (width, height), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(), content_type="image/jpeg")


def create_users():
    profile_completed_at = timezone.now()
    user1 = User.objects.create_user(
        username="user1@test.com",
        email="user1@test.com",
        password="password",
        is_verified=True,
        nickname="User1",
        onboarding_version=CURRENT_ONBOARDING_VERSION,
        profile_completed_at=profile_completed_at,
    )
    user2 = User.objects.create_user(
        username="user2@test.com",
        email="user2@test.com",
        password="password",
        is_verified=True,
        nickname="User2",
        onboarding_version=CURRENT_ONBOARDING_VERSION,
        profile_completed_at=profile_completed_at,
    )
    user3 = User.objects.create_user(
        username="user3@test.com",
        email="user3@test.com",
        password="password",
        is_verified=True,
        nickname="User3",
        onboarding_version=CURRENT_ONBOARDING_VERSION,
        profile_completed_at=profile_completed_at,
    )
    return user1, user2, user3


class UserCleanupTest(TestCase):
    def setUp(self):
        self.user1, self.user2, self.user3 = create_users()

    def test_cleanup_unverified_users_command(self):
        # Create a verified user

        # set user2 and 3 as not verified
        self.user2.is_verified = False
        self.user2.save()

        self.user3.is_verified = False
        # An abandoned registration that never finished onboarding.
        self.user3.onboarding_version = 0
        self.user3.onboarding_completed_at = None
        self.user3.profile_completed_at = None
        self.user3.save()

        # Manually set date_joined to 31 minutes ago
        self.user3.date_joined = timezone.now() - timedelta(minutes=31)
        self.user3.save()

        # Run command
        call_command("cleanup_unverified_users")

        # Check results
        self.assertTrue(User.objects.filter(email="user1@test.com").exists())
        self.assertTrue(User.objects.filter(email="user2@test.com").exists())
        self.assertFalse(User.objects.filter(email="user3@test.com").exists())


class AccessControlTest(TestCase):
    def setUp(self):

        user1, user2, _ = create_users()
        user1.is_verified = False
        user1.save()

        self.unverified_user = user1

        self.verified_user = user2

    def test_anonymous_access(self):
        """
        Test access for an unauthenticated user.
        - login/register : OK (200)
        - welcome : OK (200)
        - dashboard/account/etc : Redirect to login (302)
        """
        # OK
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)
        self.assertEqual(self.client.get(reverse("register")).status_code, 200)
        self.assertEqual(self.client.get(reverse("welcome")).status_code, 200)
        self.assertContains(self.client.get(reverse("privacy")), _("Privacy policy"))

        # Redirect to login by @login_required
        protected_urls = [
            reverse("dashboard"),
            reverse("account"),
            reverse("create_group"),
        ]
        for url in protected_urls:
            response = self.client.get(url)
            self.assertRedirects(response, reverse("login") + f"?next={url}")

    def test_unverified_user_access(self):
        """
        Test access for a logged-in but unverified user.
        - login : OK (200)
        - register : Redirect to verify_email_sent (302)
        - welcome : Redirect to verify_email_sent (302)
        - verify_email_sent/resend/account/logout : OK (200 or 302 depending on action)
        - dashboard/groups/etc : Redirect to verify_email_sent (302) via middleware
        """
        self.client.force_login(self.unverified_user)

        # Django's LoginView does not automatically redirect if accessed via GET while already logged in
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)

        # register is redirected by middleware (as it is not in allowed_urls)
        self.assertRedirects(self.client.get(reverse("register")), reverse("verify_email_sent"))

        # welcome redirects directly to verify_email_sent for unverified users
        self.assertRedirects(self.client.get(reverse("welcome")), reverse("verify_email_sent"))

        # Authorized access for unverified users
        self.assertEqual(self.client.get(reverse("verify_email_sent")).status_code, 200)
        self.assertEqual(self.client.get(reverse("account")).status_code, 200)
        self.assertContains(self.client.get(reverse("privacy")), _("Privacy policy"))

        # URLs blocked by middleware and redirected to verify_email_sent
        self.assertRedirects(self.client.get(reverse("dashboard")), reverse("verify_email_sent"))

    def test_verified_user_access(self):
        """
        Test access for a logged-in and verified user.
        - login/register/welcome : Redirect to dashboard (302)
        - verify_email_sent/resend : Redirect to dashboard (302) (since already verified)
        - dashboard/profile/groups/etc : OK (200)
        """
        self.client.force_login(self.verified_user)

        # Redirect to dashboard
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)
        self.assertRedirects(self.client.get(reverse("register")), reverse("dashboard"))
        self.assertRedirects(self.client.get(reverse("welcome")), reverse("dashboard"))

        # Redirect to dashboard as already verified
        self.assertRedirects(self.client.get(reverse("verify_email_sent")), reverse("dashboard"))
        self.assertEqual(self.client.get(reverse("resend_verification")).status_code, 405)

        # Authorized access
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("account")).status_code, 200)

        # For @require_POST views, we just test that we are not redirected by the middleware
        # (thus 405 instead of 302 to verify_email_sent)
        self.assertEqual(self.client.get(reverse("create_group")).status_code, 405)


class DashboardGiftCountTest(TestCase):
    def setUp(self):
        self.user, self.other_user, _ = create_users()

    def test_wish_count_excludes_surprises_created_by_other_users(self):
        Gift.objects.create(owner=self.user, created_by=self.user, title="My wish")
        Gift.objects.create(owner=self.user, created_by=self.other_user, title="A surprise")

        activate("en")
        try:
            self.client.force_login(self.user)
            response = self.client.get(reverse("dashboard"))
        finally:
            deactivate()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["open_wish_count"], 1)
        self.assertContains(response, "1 idea")
        self.assertNotContains(response, "2 ideas")


class PasswordResetTest(TestCase):
    def setUp(self):

        user1, _, _ = create_users()

        self.user = user1

    def test_password_reset_flow(self):
        # 1. Reset request
        response = self.client.post(reverse("account/password_reset"), {"email": "user1@test.com"})
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("account/password_reset_done"))

        # Verify that an email was sent
        self.assertEqual(len(mail.outbox), 1)
        reset_mail = mail.outbox[0]
        self.assertIn("user1@test.com", reset_mail.to)

        # Verify that the email contains HTML (since we configured html_email_template_name)
        self.assertTrue(any(alt[1] == "text/html" for alt in reset_mail.alternatives))

        # 2. Verify access to password_reset_done
        response = self.client.get(reverse("account/password_reset_done"))
        self.assertEqual(response.status_code, 200)

    def test_password_reset_unverified_user(self):
        self.user.is_verified = False
        self.user.save()

        self.client.force_login(self.user)
        # Should not be redirected to verify_email_sent by the middleware
        response = self.client.get(reverse("account/password_reset"))
        self.assertEqual(response.status_code, 200)

    def test_password_reset_confirm_unverified_user(self):
        self.user.is_verified = False
        self.user.save()

        url = reverse("account/password_reset_confirm", kwargs={"uidb64": "MQ", "token": "abc-123"})

        self.client.force_login(self.user)
        # Should not be redirected to verify_email_sent by the middleware
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)


class GiftAccessControlTest(TestCase):
    def setUp(self):

        self.user1, self.user2, self.user3 = create_users()

        # Group between User1 and User2
        self.group = Group.objects.create(name="Group 1-2")
        self.group.members.add(self.user1, self.user2)

        # Gift from User1 (created by himself)
        self.gift_user1 = Gift.objects.create(owner=self.user1, created_by=self.user1, title="Gift User1")

        # Surprise for User1 created by User2
        self.surprise_user1 = Gift.objects.create(owner=self.user1, created_by=self.user2, title="Surprise User1")

    def test_view_list_access(self):
        """A user can only access lists of himself or people with whom he shares at least one group"""
        self.client.force_login(self.user2)
        # User2 shares a group with User1
        self.assertEqual(self.client.get(reverse("view_list", args=[self.user1.id])).status_code, 200)

        self.client.force_login(self.user3)
        # User3 does not share a group with User1
        self.assertEqual(self.client.get(reverse("view_list", args=[self.user1.id])).status_code, 403)

    def test_add_gift_modal_title_matches_list_type(self):
        self.client.force_login(self.user1)
        own_list = self.client.get(reverse("view_list", args=[self.user1.id]))
        self.assertContains(own_list, 'data-bs-target="#addGiftModal"')
        self.assertContains(own_list, _("New wish"))

        self.client.force_login(self.user2)
        another_users_list = self.client.get(reverse("view_list", args=[self.user1.id]))
        self.assertContains(another_users_list, 'data-bs-target="#addGiftModal"')
        self.assertContains(another_users_list, _("New surprise"))

        managed_user = User.objects.create_user(
            username="managed@test.com",
            email="managed@test.com",
            password="password",
            is_verified=True,
            is_managed=True,
            nickname="Managed",
        )
        self.group.members.add(managed_user)
        ManagedMember.objects.create(name="Managed", group=self.group, color="#000000", user=managed_user)

        managed_list = self.client.get(reverse("view_list", args=[managed_user.id]))
        self.assertContains(managed_list, 'data-bs-target="#addGiftModal"')
        self.assertContains(managed_list, _("New wish"))

    def test_add_surprise_modal_only_lists_groups_shared_with_owner(self):
        group_without_owner = Group.objects.create(name="Group 2-3")
        group_without_owner.members.add(self.user2, self.user3)

        self.client.force_login(self.user2)
        response = self.client.get(reverse("view_list", args=[self.user1.id]))

        self.assertContains(response, f'id="groupAdd{self.group.id}"')
        self.assertNotContains(response, f'id="groupAdd{group_without_owner.id}"')

    def test_add_surprise_rejects_group_not_shared_with_owner(self):
        group_without_owner = Group.objects.create(name="Group 2-3")
        group_without_owner.members.add(self.user2, self.user3)

        self.client.force_login(self.user2)
        response = self.client.post(
            reverse("add_gift", args=[self.user1.id]),
            {"title": "Hidden surprise", "visible_in": [group_without_owner.id]},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Gift.objects.filter(title="Hidden surprise").exists())

    def test_add_surprise_accepts_group_shared_with_owner(self):
        self.client.force_login(self.user2)
        response = self.client.post(
            reverse("add_gift", args=[self.user1.id]),
            {"title": "Shared surprise", "visible_in": [self.group.id]},
        )

        self.assertEqual(response.status_code, 302)
        surprise = Gift.objects.get(title="Shared surprise")
        self.assertEqual(list(surprise.visible_in.all()), [self.group])

    def test_add_gift_accepts_multiple_fixed_tags(self):
        self.client.force_login(self.user1)
        response = self.client.post(
            reverse("add_gift", args=[self.user1.id]),
            {"title": "Tagged gift", "tags": [GiftTag.Slug.BOOKS, GiftTag.Slug.ART_DECOR]},
        )

        self.assertEqual(response.status_code, 302)
        gift = Gift.objects.get(title="Tagged gift")
        self.assertEqual(
            set(gift.tags.values_list("slug", flat=True)),
            {GiftTag.Slug.BOOKS, GiftTag.Slug.ART_DECOR},
        )

    def test_owner_can_add_private_draft_in_separate_section(self):
        self.client.force_login(self.user1)
        response = self.client.post(
            reverse("add_gift", args=[self.user1.id]),
            {"title": "Unfinished idea", "is_draft": "1", "visible_in": [self.group.id]},
        )

        self.assertEqual(response.status_code, 302)
        draft = Gift.objects.get(title="Unfinished idea")
        self.assertTrue(draft.is_draft)
        self.assertFalse(draft.visible_in.exists())

        own_list = self.client.get(reverse("view_list", args=[self.user1.id]))
        self.assertContains(own_list, _("Drafts"))
        self.assertContains(own_list, draft.title)

        self.client.force_login(self.user2)
        other_list = self.client.get(reverse("view_list", args=[self.user1.id]))
        self.assertNotContains(other_list, draft.title)

    def test_non_owner_cannot_mark_surprise_as_draft(self):
        self.client.force_login(self.user2)
        self.client.post(
            reverse("add_gift", args=[self.user1.id]),
            {"title": "Published surprise", "is_draft": "1", "visible_in": [self.group.id]},
        )

        surprise = Gift.objects.get(title="Published surprise")
        self.assertFalse(surprise.is_draft)
        self.assertEqual(list(surprise.visible_in.all()), [self.group])

    def test_editing_draft_can_publish_it_to_a_group(self):
        draft = Gift.objects.create(
            owner=self.user1,
            created_by=self.user1,
            title="Draft to publish",
            is_draft=True,
        )
        self.client.force_login(self.user1)

        response = self.client.post(
            reverse("edit_gift", args=[draft.id]),
            {"title": draft.title, "visible_in": [self.group.id]},
        )

        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db()
        self.assertFalse(draft.is_draft)
        self.assertEqual(list(draft.visible_in.all()), [self.group])

    def test_moving_published_gift_to_drafts_removes_group_state(self):
        self.gift_user1.visible_in.add(self.group)
        self.gift_user1.group_reserved_on = self.group
        self.gift_user1.save()
        Reservation.objects.create(gift=self.gift_user1, reserver=self.user2)
        self.client.force_login(self.user1)

        self.client.post(
            reverse("edit_gift", args=[self.gift_user1.id]),
            {"title": self.gift_user1.title, "is_draft": "1", "visible_in": [self.group.id]},
        )

        self.gift_user1.refresh_from_db()
        self.assertTrue(self.gift_user1.is_draft)
        self.assertIsNone(self.gift_user1.group_reserved_on)
        self.assertFalse(self.gift_user1.visible_in.exists())
        self.assertFalse(self.gift_user1.reservation.exists())

    def test_add_gift_rejects_unknown_tag(self):
        self.client.force_login(self.user1)
        response = self.client.post(
            reverse("add_gift", args=[self.user1.id]),
            {"title": "Invalid tagged gift", "tags": ["user_created_tag"]},
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Gift.objects.filter(title="Invalid tagged gift").exists())

    def test_edit_gift_replaces_tags(self):
        self.gift_user1.tags.add(GiftTag.objects.get(slug=GiftTag.Slug.BOOKS))
        self.client.force_login(self.user1)
        response = self.client.post(
            reverse("edit_gift", args=[self.gift_user1.id]),
            {"title": self.gift_user1.title, "tags": [GiftTag.Slug.TECHNOLOGY]},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(self.gift_user1.tags.values_list("slug", flat=True)), [GiftTag.Slug.TECHNOLOGY])

    def test_list_filters_wishes_and_surprises_by_tag(self):
        self.gift_user1.tags.add(GiftTag.objects.get(slug=GiftTag.Slug.BOOKS))
        self.surprise_user1.tags.add(GiftTag.objects.get(slug=GiftTag.Slug.TECHNOLOGY))
        self.client.force_login(self.user2)

        response = self.client.get(
            reverse("view_list", args=[self.user1.id]),
            {"tags": GiftTag.Slug.TECHNOLOGY},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["gift"] for item in response.context["gifts"]], [])
        self.assertEqual([item["gift"] for item in response.context["surprises"]], [self.surprise_user1])
        self.assertContains(response, _("Technology"))

    def test_list_can_sort_wishes_by_title(self):
        second_gift = Gift.objects.create(owner=self.user1, created_by=self.user1, title="A first gift")
        self.client.force_login(self.user2)

        response = self.client.get(reverse("view_list", args=[self.user1.id]), {"sort": "title"})

        self.assertEqual(
            [item["gift"] for item in response.context["gifts"]],
            [second_gift, self.gift_user1],
        )

    def test_edit_gift_access(self):
        """A user can only edit his gifts or surprises from groups he is in"""
        # User1 edits his own gift
        self.client.force_login(self.user1)
        response = self.client.post(reverse("edit_gift", args=[self.gift_user1.id]), {"title": "Updated Title"})
        self.assertEqual(response.status_code, 302)
        self.gift_user1.refresh_from_db()
        self.assertEqual(self.gift_user1.title, "Updated Title")

        # User2 edits the surprise he created for User1
        self.client.force_login(self.user2)
        response = self.client.post(reverse("edit_gift", args=[self.surprise_user1.id]), {"title": "Updated Surprise"})
        self.assertEqual(response.status_code, 302)
        self.surprise_user1.refresh_from_db()
        self.assertEqual(self.surprise_user1.title, "Updated Surprise")

        # User3 attempts to edit User1's gift (should fail)
        self.client.force_login(self.user3)
        response = self.client.post(reverse("edit_gift", args=[self.gift_user1.id]), {"title": "Hacked Title"})
        # Currently this probably passes (200 or 302), we expect 403 or 404
        self.assertIn(response.status_code, [403, 404])

    def test_delete_gift_access(self):
        """A user can only delete his gifts or surprises from groups he is in"""
        # User3 attempts to delete User1's gift
        self.client.force_login(self.user3)
        response = self.client.post(reverse("delete_gift", args=[self.gift_user1.id]))
        self.assertIn(response.status_code, [403, 404])
        self.assertTrue(Gift.objects.filter(id=self.gift_user1.id).exists())


class ReservationFlowTest(TestCase):
    def setUp(self):
        self.user1, self.user2, self.user3 = create_users()
        self.group = Group.objects.create(name="Group 1-2")
        self.group.members.add(self.user1, self.user2, self.user3)
        self.gift = Gift.objects.create(owner=self.user1, created_by=self.user1, title="Shared Gift")

    def _json_post(self, url, payload):
        return self.client.post(url, data=json.dumps(payload), content_type="application/json")

    def test_list_shows_open_participation_state_for_other_reservations(self):
        Reservation.objects.create(gift=self.gift, reserver=self.user3)
        self.gift.group_reserved_on = self.group
        self.gift.save()

        self.client.force_login(self.user2)
        response = self.client.get(f"{reverse('view_list', args=[self.user1.id])}?from_group={self.group.id}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "nc-wish-status--participating_by_others")
        self.assertContains(response, "nc-wish-reserve-btn--participating_by_others")

    def test_list_shows_exclusive_state_for_other_reservation(self):
        Reservation.objects.create(gift=self.gift, reserver=self.user3, exclusivity=True)
        self.gift.group_reserved_on = self.group
        self.gift.save()

        self.client.force_login(self.user2)
        response = self.client.get(f"{reverse('view_list', args=[self.user1.id])}?from_group={self.group.id}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "nc-wish-status--reserved_by_other_exclusive")

    def test_list_without_group_context_still_sets_reservation_group(self):
        self.client.force_login(self.user2)
        response = self.client.get(reverse("view_list", args=[self.user1.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f", {self.group.id})")

    def test_reserve_rejects_user_outside_group(self):
        outsider = User.objects.create_user(
            username="outsider@test.com",
            email="outsider@test.com",
            password="password",
            is_verified=True,
            nickname="Outsider",
            onboarding_version=CURRENT_ONBOARDING_VERSION,
            profile_completed_at=timezone.now(),
        )

        self.client.force_login(self.user2)
        response = self._json_post(
            reverse("reserve_gift", args=[self.gift.id]),
            {"exclusivity": False, "user_id": outsider.id, "group_id": self.group.id},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Reservation.objects.filter(gift=self.gift, reserver=outsider).exists())

    def test_delete_one_participation_keeps_group_when_others_remain(self):
        Reservation.objects.create(gift=self.gift, reserver=self.user2)
        Reservation.objects.create(gift=self.gift, reserver=self.user3)
        self.gift.group_reserved_on = self.group
        self.gift.save()

        self.client.force_login(self.user2)
        response = self._json_post(
            reverse("delete_reservation", args=[self.gift.id]),
            {"reservation_user_id_to_delete": self.user2.id, "group_id": self.group.id},
        )

        self.assertEqual(response.status_code, 200)
        self.gift.refresh_from_db()
        self.assertEqual(self.gift.group_reserved_on, self.group)
        self.assertFalse(Reservation.objects.filter(gift=self.gift, reserver=self.user2).exists())
        self.assertTrue(Reservation.objects.filter(gift=self.gift, reserver=self.user3).exists())

    def test_delete_last_participation_clears_group(self):
        Reservation.objects.create(gift=self.gift, reserver=self.user2)
        self.gift.group_reserved_on = self.group
        self.gift.save()

        self.client.force_login(self.user2)
        response = self._json_post(
            reverse("delete_reservation", args=[self.gift.id]),
            {"reservation_user_id_to_delete": self.user2.id, "group_id": self.group.id},
        )

        self.assertEqual(response.status_code, 200)
        self.gift.refresh_from_db()
        self.assertIsNone(self.gift.group_reserved_on)


class GiftCommentTest(TestCase):
    def setUp(self):
        self.owner, self.member, self.other_member = create_users()
        self.outsider = User.objects.create_user(
            username="outsider@test.com",
            email="outsider@test.com",
            password="password",
            is_verified=True,
            nickname="Outsider",
            onboarding_version=CURRENT_ONBOARDING_VERSION,
            profile_completed_at=timezone.now(),
        )
        self.group = Group.objects.create(name="Family", created_by=self.member)
        self.group.members.add(self.owner, self.member, self.other_member)
        self.gift = Gift.objects.create(owner=self.owner, created_by=self.owner, title="Bike")

    def test_group_member_can_add_secret_comment(self):
        self.client.force_login(self.member)

        response = self.client.post(
            reverse("add_gift_comment", args=[self.gift.id]),
            {"group_id": self.group.id, "body": "Found cheaper here"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            GiftComment.objects.filter(
                gift=self.gift,
                group=self.group,
                author=self.member,
                body="Found cheaper here",
            ).exists()
        )

    def test_owner_cannot_read_or_add_comments_on_their_gift(self):
        GiftComment.objects.create(gift=self.gift, group=self.group, author=self.member, body="Secret note")
        self.client.force_login(self.owner)

        list_response = self.client.get(reverse("view_list", args=[self.owner.id]))
        add_response = self.client.post(
            reverse("add_gift_comment", args=[self.gift.id]),
            {"group_id": self.group.id, "body": "Owner note"},
        )

        self.assertNotContains(list_response, "Secret note")
        self.assertEqual(add_response.status_code, 403)
        self.assertFalse(GiftComment.objects.filter(author=self.owner).exists())

    def test_group_member_sees_comments_in_gift_detail_modal(self):
        GiftComment.objects.create(gift=self.gift, group=self.group, author=self.other_member, body="Already bought")
        self.client.force_login(self.member)

        response = self.client.get(f"{reverse('view_list', args=[self.owner.id])}?from_group={self.group.id}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'id="viewGiftModal{self.gift.id}"')
        self.assertContains(response, _("Secret group notes"))
        self.assertContains(response, "Already bought")

    def test_author_edit_form_is_hidden_until_edit_button_is_clicked(self):
        comment = GiftComment.objects.create(gift=self.gift, group=self.group, author=self.member, body="Draft")
        self.client.force_login(self.member)

        response = self.client.get(f"{reverse('view_list', args=[self.owner.id])}?from_group={self.group.id}")

        self.assertContains(response, f'onclick="toggleGiftCommentEdit({comment.id})"')
        self.assertContains(response, f'id="editGiftCommentForm{comment.id}"')
        self.assertContains(response, "d-none")

    def test_comments_are_scoped_to_group(self):
        second_group = Group.objects.create(name="Friends")
        second_group.members.add(self.owner, self.outsider)
        GiftComment.objects.create(gift=self.gift, group=self.group, author=self.member, body="Family note")

        self.client.force_login(self.outsider)
        response = self.client.get(f"{reverse('view_list', args=[self.owner.id])}?from_group={second_group.id}")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Family note")

    def test_outsider_cannot_add_comment(self):
        self.client.force_login(self.outsider)

        response = self.client.post(
            reverse("add_gift_comment", args=[self.gift.id]),
            {"group_id": self.group.id, "body": "No access"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(GiftComment.objects.exists())

    def test_author_can_soft_delete_comment(self):
        comment = GiftComment.objects.create(gift=self.gift, group=self.group, author=self.member, body="Old note")
        self.client.force_login(self.member)

        response = self.client.post(reverse("delete_gift_comment", args=[comment.id]))

        self.assertEqual(response.status_code, 302)
        comment.refresh_from_db()
        self.assertTrue(comment.is_deleted)
        self.assertEqual(comment.deleted_by, self.member)

        list_response = self.client.get(f"{reverse('view_list', args=[self.owner.id])}?from_group={self.group.id}")
        self.assertContains(list_response, _("Comment deleted."))
        self.assertNotContains(list_response, "Old note")

    def test_ajax_delete_comment_returns_updated_panel_without_redirect(self):
        comment = GiftComment.objects.create(gift=self.gift, group=self.group, author=self.member, body="Old note")
        self.client.force_login(self.member)

        response = self.client.post(
            reverse("delete_gift_comment", args=[comment.id]),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'id="giftCommentsPanel{self.gift.id}"')
        self.assertContains(response, _("Comment deleted."))
        self.assertNotContains(response, "Old note")

    def test_group_creator_cannot_delete_someone_elses_comment(self):
        comment = GiftComment.objects.create(
            gift=self.gift,
            group=self.group,
            author=self.other_member,
            body="Not mine",
        )
        self.client.force_login(self.member)

        response = self.client.post(reverse("delete_gift_comment", args=[comment.id]))

        self.assertEqual(response.status_code, 403)
        comment.refresh_from_db()
        self.assertFalse(comment.is_deleted)

    def test_author_can_edit_comment_and_badge_is_shown(self):
        comment = GiftComment.objects.create(gift=self.gift, group=self.group, author=self.member, body="Initial note")
        self.client.force_login(self.member)

        response = self.client.post(
            reverse("edit_gift_comment", args=[comment.id]),
            {"body": "Updated note"},
        )

        self.assertEqual(response.status_code, 302)
        comment.refresh_from_db()
        self.assertEqual(comment.body, "Updated note")
        self.assertIsNotNone(comment.edited_at)

        list_response = self.client.get(f"{reverse('view_list', args=[self.owner.id])}?from_group={self.group.id}")
        self.assertContains(list_response, "Updated note")
        self.assertContains(list_response, _("edited"))
        self.assertNotContains(list_response, "Initial note")

    def test_ajax_edit_comment_returns_updated_panel_without_redirect(self):
        comment = GiftComment.objects.create(gift=self.gift, group=self.group, author=self.member, body="Initial note")
        self.client.force_login(self.member)

        response = self.client.post(
            reverse("edit_gift_comment", args=[comment.id]),
            {"body": "Updated note"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'id="giftCommentsPanel{self.gift.id}"')
        self.assertContains(response, "Updated note")
        self.assertContains(response, _("edited"))
        self.assertNotContains(response, "Initial note")

    def test_ajax_add_comment_returns_updated_panel_without_redirect(self):
        self.client.force_login(self.member)

        response = self.client.post(
            reverse("add_gift_comment", args=[self.gift.id]),
            {"group_id": self.group.id, "body": "Fresh note"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'id="giftCommentsPanel{self.gift.id}"')
        self.assertContains(response, "Fresh note")

    def test_other_member_cannot_edit_comment(self):
        comment = GiftComment.objects.create(
            gift=self.gift, group=self.group, author=self.other_member, body="Original"
        )
        self.client.force_login(self.member)

        response = self.client.post(
            reverse("edit_gift_comment", args=[comment.id]),
            {"body": "Hacked"},
        )

        self.assertEqual(response.status_code, 403)
        comment.refresh_from_db()
        self.assertEqual(comment.body, "Original")

    def test_deleted_comment_cannot_be_edited(self):
        comment = GiftComment.objects.create(
            gift=self.gift,
            group=self.group,
            author=self.member,
            body="Deleted",
            is_deleted=True,
        )
        self.client.force_login(self.member)

        response = self.client.post(
            reverse("edit_gift_comment", args=[comment.id]),
            {"body": "Back"},
        )

        self.assertEqual(response.status_code, 403)
        comment.refresh_from_db()
        self.assertEqual(comment.body, "Deleted")


class SubscriptionTest(TestCase):
    def setUp(self):

        self.user1, self.user2, self.user3 = create_users()
        self.group = Group.objects.create(name="Group 1-2")
        self.group.members.add(self.user1, self.user2)

    def test_toggle_subscription(self):
        self.client.force_login(self.user2)
        # Subscribe
        response = self.client.post(reverse("toggle_subscription", args=[self.user1.id]))
        self.assertRedirects(response, reverse("view_list", args=[self.user1.id]))
        self.assertTrue(self.user2.subscriptions.filter(id=self.user1.id).exists())

        # Unsubscribe
        response = self.client.post(reverse("toggle_subscription", args=[self.user1.id]))
        self.assertRedirects(response, reverse("view_list", args=[self.user1.id]))
        self.assertFalse(self.user2.subscriptions.filter(id=self.user1.id).exists())

    def test_toggle_subscription_preserves_group_context(self):
        self.client.force_login(self.user2)
        list_url = reverse("view_list", args=[self.user1.id])
        group_list_url = f"{list_url}?from_group={self.group.id}"

        response = self.client.post(
            reverse("toggle_subscription", args=[self.user1.id]),
            HTTP_REFERER=group_list_url,
        )

        self.assertRedirects(response, group_list_url)

    def test_rss_only_subscription_disables_email(self):
        self.client.force_login(self.user2)
        self.client.post(
            reverse("toggle_subscription", args=[self.user1.id]),
            {"delivery": "rss"},
        )
        subscription = Subscription.objects.get(subscriber=self.user2, owner=self.user1)
        self.assertFalse(subscription.email_enabled)
        self.assertTrue(subscription.rss_enabled)

        self.client.force_login(self.user1)
        self.client.post(reverse("add_gift", args=[self.user1.id]), {"title": "RSS only"})
        self.assertEqual(len(mail.outbox), 0)

    def test_event_reminder_preferences_are_saved(self):
        self.user1.birthday = date(1990, 8, 8)
        self.user1.save()
        self.client.force_login(self.user2)

        self.client.post(
            reverse("toggle_subscription", args=[self.user1.id]),
            {"delivery": "email", "birthday_reminder": "on", "christmas_reminder": "on"},
        )

        subscription = Subscription.objects.get(subscriber=self.user2, owner=self.user1)
        self.assertTrue(subscription.birthday_reminder)
        self.assertTrue(subscription.christmas_reminder)

    def test_profile_birthday_saves_and_displays_without_year(self):
        self.client.force_login(self.user1)

        self.client.post(
            reverse("account"),
            {
                "nickname": self.user1.nickname,
                "email": self.user1.email,
                "birthday_month": "8",
                "birthday_day": "8",
            },
        )

        self.user1.refresh_from_db()
        self.assertEqual(self.user1.birthday_month, 8)
        self.assertEqual(self.user1.birthday_day, 8)

        response = self.client.get(reverse("account"))
        self.assertContains(response, '<option value="8" selected>August</option>', html=True)
        self.assertContains(response, '<option value="8" selected>8</option>', html=True)

    @override_settings(PUBLIC_BASE_URL="https://example.test")
    def test_birthday_reminder_is_sent_once_two_weeks_before(self):
        self.user1.birthday = date(1990, 8, 8)
        self.user1.save()
        Gift.objects.create(owner=self.user1, created_by=self.user1, title="Birthday gift")
        Subscription.objects.create(
            subscriber=self.user2,
            owner=self.user1,
            birthday_reminder=True,
        )

        call_command("send_event_reminders", date="2026-07-25")
        call_command("send_event_reminders", date="2026-07-25")

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("deux semaines", mail.outbox[0].subject)
        self.assertIn("Birthday gift", mail.outbox[0].body)
        self.assertIn("https://example.test", mail.outbox[0].body)

    def test_christmas_reminder_is_sent_on_november_25(self):
        Subscription.objects.create(
            subscriber=self.user2,
            owner=self.user1,
            christmas_reminder=True,
        )

        call_command("send_event_reminders", date="2026-11-25")

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Noel", mail.outbox[0].subject)

    def test_private_rss_feed_respects_gift_visibility(self):
        group2 = Group.objects.create(name="Group 1-3")
        group2.members.add(self.user1, self.user3)
        public_gift = Gift.objects.create(owner=self.user1, created_by=self.user1, title="Public wish")
        private_gift = Gift.objects.create(owner=self.user1, created_by=self.user1, title="Private wish")
        private_gift.visible_in.add(group2)
        subscription = Subscription.objects.create(
            subscriber=self.user2,
            owner=self.user1,
            email_enabled=False,
            rss_enabled=True,
        )

        self.client.logout()
        response = self.client.get(reverse("subscription_feed", args=[subscription.feed_token]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, public_gift.title)
        self.assertNotContains(response, private_gift.title)
        self.assertIn("application/rss+xml", response["Content-Type"])

    def test_private_rss_feed_excludes_drafts(self):
        draft = Gift.objects.create(
            owner=self.user1,
            created_by=self.user1,
            title="Private draft",
            is_draft=True,
        )
        subscription = Subscription.objects.create(
            subscriber=self.user2,
            owner=self.user1,
            email_enabled=False,
            rss_enabled=True,
        )

        response = self.client.get(reverse("subscription_feed", args=[subscription.feed_token]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, draft.title)

    def test_unsubscribe_revokes_private_rss_feed(self):
        subscription = Subscription.objects.create(
            subscriber=self.user2,
            owner=self.user1,
            email_enabled=False,
            rss_enabled=True,
        )
        feed_url = reverse("subscription_feed", args=[subscription.feed_token])
        self.client.force_login(self.user2)

        self.client.post(
            reverse("toggle_subscription", args=[self.user1.id]),
            {"action": "unsubscribe"},
        )

        self.assertEqual(self.client.get(feed_url).status_code, 404)

    def test_subscribe_self(self):
        self.client.force_login(self.user1)
        response = self.client.post(reverse("toggle_subscription", args=[self.user1.id]))
        self.assertEqual(response.status_code, 403)

    def test_subscribe_no_common_group(self):
        self.client.force_login(self.user3)
        response = self.client.post(reverse("toggle_subscription", args=[self.user1.id]))
        self.assertEqual(response.status_code, 403)

    def test_notification_sent_on_add_gift(self):
        # User2 subscribes to User1
        self.user2.subscriptions.add(self.user1)

        self.client.force_login(self.user1)
        # User1 adds a gift to his own list
        response = self.client.post(
            reverse("add_gift", args=[self.user1.id]),
            {"title": "New Gift", "description": "A cool gift", "url": "https://example.com"},
        )
        self.assertRedirects(response, reverse("view_list", args=[self.user1.id]))

        # Verify email delivery
        self.assertEqual(len(mail.outbox), 1)
        notification_mail = mail.outbox[0]
        self.assertIn(self.user2.email, notification_mail.to)
        # The subject is translated to French by default in tests because LANGUAGE_CODE='fr'
        self.assertIn("User1", notification_mail.subject)
        self.assertIn("New Gift", notification_mail.body)
        self.assertIn("A cool gift", notification_mail.body)
        self.assertIn("https://example.com", notification_mail.body)

        # Verify unsubscribe link
        self.assertIn("/unsubscribe/", notification_mail.body)

    def test_notification_not_sent_when_owner_adds_draft(self):
        self.user2.subscriptions.add(self.user1)
        self.client.force_login(self.user1)

        self.client.post(
            reverse("add_gift", args=[self.user1.id]),
            {"title": "Quiet draft", "is_draft": "1"},
        )

        self.assertEqual(len(mail.outbox), 0)

    def test_unsubscribe_token(self):
        self.user2.subscriptions.add(self.user1)

        uid = urlsafe_base64_encode(force_bytes(self.user2.pk))
        token = default_token_generator.make_token(self.user2)
        url = reverse("unsubscribe_token", args=[self.user1.pk, uid, token])

        # Email links work without requiring an authenticated browser session.
        response = self.client.get(url)
        self.assertRedirects(
            response,
            reverse("view_list", args=[self.user1.id]),
            fetch_redirect_response=False,
        )
        self.assertFalse(self.user2.subscriptions.filter(id=self.user1.id).exists())

    def test_unsubscribe_rejects_invalid_token(self):
        self.user2.subscriptions.add(self.user1)

        uid = urlsafe_base64_encode(force_bytes(self.user2.pk))
        url = reverse("unsubscribe_token", args=[self.user1.pk, uid, "invalid-token"])

        self.client.get(url)

        self.assertTrue(self.user2.subscriptions.filter(id=self.user1.id).exists())

    def test_notification_not_sent_when_subscriber_adds_surprise(self):
        # User2 subscribes to User1
        self.user2.subscriptions.add(self.user1)

        self.client.force_login(self.user2)
        # User2 add a surprise to User1's list
        self.client.post(reverse("add_gift", args=[self.user1.id]), {"title": "Surprise"})

        self.assertEqual(len(mail.outbox), 0)

    def test_custom_birthday_reminder_delay_is_saved_and_used(self):
        self.user1.birthday = date(1990, 8, 8)
        self.user1.save()
        self.client.force_login(self.user2)

        self.client.post(
            reverse("toggle_subscription", args=[self.user1.id]),
            {
                "delivery": "email",
                "birthday_reminder": "on",
                "birthday_reminder_days_before": "7",
            },
        )

        subscription = Subscription.objects.get(subscriber=self.user2, owner=self.user1)
        self.assertEqual(subscription.birthday_reminder_days_before, 7)

        call_command("send_event_reminders", date="2026-08-01")
        self.assertEqual(len(mail.outbox), 1)

    def test_notification_center_updates_subscription_and_digest(self):
        subscription = Subscription.objects.create(
            subscriber=self.user2,
            owner=self.user1,
            birthday_reminder=True,
            christmas_reminder=True,
        )
        self.client.force_login(self.user2)

        response = self.client.get(reverse("notification_center"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.user1.nickname)

        self.client.post(
            reverse("notification_center"),
            {
                "action": "update_subscription",
                "subscription_id": subscription.id,
                "delivery": "both",
                "birthday_reminder": "on",
                "birthday_reminder_days_before": "1",
                "christmas_reminder": "on",
                "christmas_reminder_days_before": "7",
            },
        )
        subscription.refresh_from_db()
        self.assertTrue(subscription.email_enabled)
        self.assertTrue(subscription.rss_enabled)
        self.assertEqual(subscription.birthday_reminder_days_before, 1)
        self.assertEqual(subscription.christmas_reminder_days_before, 7)

        self.client.post(
            reverse("notification_center"),
            {"action": "update_digest", "frequency": NotificationDigestPreference.FREQUENCY_WEEKLY},
        )
        digest_preference = NotificationDigestPreference.objects.get(user=self.user2)
        self.assertEqual(digest_preference.frequency, NotificationDigestPreference.FREQUENCY_WEEKLY)

    def test_notification_digest_command_sends_due_digest(self):
        NotificationDigestPreference.objects.create(
            user=self.user2,
            frequency=NotificationDigestPreference.FREQUENCY_DAILY,
        )
        gift = Gift.objects.create(owner=self.user1, created_by=self.user1, title="Visible group wish")
        gift.visible_in.add(self.group)

        call_command("send_notification_digests")

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Visible group wish", mail.outbox[0].body)

    def test_notification_visibility_restriction(self):
        # User2 subscribes to User1
        self.user2.subscriptions.add(self.user1)

        # User1 create another group with User3
        group2 = Group.objects.create(name="Group 1-3")
        group2.members.add(self.user1, self.user3)

        self.client.force_login(self.user1)

        # User1 add a gift visible only to 1-3 group
        self.client.post(reverse("add_gift", args=[self.user1.id]), {"title": "Secret Gift", "visible_in": [group2.id]})

        # User2 shouldn't receive a notification
        self.assertEqual(len(mail.outbox), 0)

        # User3 subscribes to User1
        self.user3.subscriptions.add(self.user1)
        mail.outbox = []

        # User1 add a gift visible to everybody
        self.client.post(reverse("add_gift", args=[self.user1.id]), {"title": "Public Gift"})

        # User2 and User3 receive a mail
        self.assertEqual(len(mail.outbox), 2)

    def test_add_gift_access(self):
        """Add a gift/surprise only if there is a common group"""
        self.client.force_login(self.user3)
        # User3 attempts to add a gift to User1
        response = self.client.post(reverse("add_gift", args=[self.user1.id]), {"title": "Bad Surprise"})
        self.assertIn(response.status_code, [403, 404])
        self.assertFalse(Gift.objects.filter(title="Bad Surprise").exists())


class GroupManagementTest(TestCase):
    def setUp(self):

        self.creator, self.member, self.outsider = create_users()

        self.group = Group.objects.create(name="Original Name", created_by=self.creator)
        self.group.members.add(self.creator, self.member)
        self.original_token = self.group.group_token

    def test_edit_group_name_as_creator(self):
        self.client.force_login(self.creator)
        response = self.client.post(
            reverse("edit_group", args=[self.group.id]), {"name": "New Name", "description": ""}
        )
        self.assertEqual(response.status_code, 302)
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, "New Name")

    def test_edit_group_name_as_member(self):
        self.client.force_login(self.member)
        response = self.client.post(
            reverse("edit_group", args=[self.group.id]), {"name": "New Name", "description": ""}
        )
        self.assertEqual(response.status_code, 302)
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, "New Name")

    def test_regenerate_token_as_creator(self):
        self.client.force_login(self.creator)
        response = self.client.post(reverse("regenerate_group_token", args=[self.group.id]))
        self.assertEqual(response.status_code, 302)
        self.group.refresh_from_db()
        self.assertNotEqual(self.group.group_token, self.original_token)
        self.assertTrue(len(self.group.group_token) > 0)

    def test_regenerate_token_as_member(self):
        self.client.force_login(self.member)
        response = self.client.post(reverse("regenerate_group_token", args=[self.group.id]))
        self.assertEqual(response.status_code, 302)
        self.group.refresh_from_db()
        self.assertNotEqual(self.group.group_token, self.original_token)
        self.assertTrue(len(self.group.group_token) > 0)


class GroupImageTest(TestCase):
    def setUp(self):
        self.user, self.member, self.outsider = create_users()
        self.group = Group.objects.create(name="Photo Group", created_by=self.user)
        self.group.members.add(self.user, self.member)
        self.media_dir = tempfile.mkdtemp()
        self.static_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.media_dir, ignore_errors=True)
        shutil.rmtree(self.static_dir, ignore_errors=True)

    def test_upload_image_as_member(self):
        """A member can upload a group image."""
        self.client.force_login(self.member)
        with override_settings(MEDIA_ROOT=self.media_dir):
            response = self.client.post(
                reverse("edit_group", args=[self.group.id]),
                {"name": "Photo Group", "description": "", "image": make_image()},
            )
        self.assertEqual(response.status_code, 302)
        self.group.refresh_from_db()
        self.assertTrue(self.group.image)
        self.assertIn(f"groups/{self.group.id}/", self.group.image.name)

    def test_image_filename_uses_uuid(self):
        """The stored filename is a UUID, not the original upload name."""
        self.client.force_login(self.user)
        with override_settings(MEDIA_ROOT=self.media_dir):
            self.client.post(
                reverse("edit_group", args=[self.group.id]),
                {"name": "Photo Group", "description": "", "image": make_image("my_photo.jpg")},
            )
        self.group.refresh_from_db()
        filename = os.path.basename(self.group.image.name)
        self.assertNotEqual(filename, "my_photo.jpg")

    def test_replace_image_removes_old_file(self):
        """Uploading a new image deletes the previous file from disk."""
        self.client.force_login(self.user)
        with override_settings(MEDIA_ROOT=self.media_dir):
            self.client.post(
                reverse("edit_group", args=[self.group.id]),
                {"name": "Photo Group", "description": "", "image": make_image("first.jpg")},
            )
            self.group.refresh_from_db()
            old_path = self.group.image.path

            self.client.post(
                reverse("edit_group", args=[self.group.id]),
                {"name": "Photo Group", "description": "", "image": make_image("second.jpg")},
            )
            self.assertFalse(os.path.isfile(old_path))

        self.group.refresh_from_db()
        self.assertTrue(self.group.image)

    def test_edit_without_image_keeps_existing(self):
        """Editing name/description without a new image preserves the existing image."""
        self.client.force_login(self.user)
        with override_settings(MEDIA_ROOT=self.media_dir):
            self.client.post(
                reverse("edit_group", args=[self.group.id]),
                {"name": "Photo Group", "description": "", "image": make_image()},
            )
            self.group.refresh_from_db()
            old_image_name = self.group.image.name

            self.client.post(
                reverse("edit_group", args=[self.group.id]),
                {"name": "New Name", "description": "New desc"},
            )

        self.group.refresh_from_db()
        self.assertEqual(self.group.image.name, old_image_name)
        self.assertEqual(self.group.name, "New Name")

    def test_outsider_cannot_upload_image(self):
        """A non-member is redirected to dashboard and the group is unchanged."""
        self.client.force_login(self.outsider)
        with override_settings(MEDIA_ROOT=self.media_dir):
            response = self.client.post(
                reverse("edit_group", args=[self.group.id]),
                {"name": "Hacked", "description": "", "image": make_image()},
            )
        self.assertRedirects(response, reverse("dashboard"))
        self.group.refresh_from_db()
        self.assertFalse(self.group.image)
        self.assertEqual(self.group.name, "Photo Group")

    def test_choose_group_preset_stores_reference_without_copy(self):
        """Choosing a preset stores its static path instead of copying a media file."""
        preset_path = "gifts/presets/group/family.jpg"
        os.makedirs(os.path.join(self.static_dir, "gifts", "presets", "group"), exist_ok=True)
        with open(os.path.join(self.static_dir, preset_path), "wb") as preset_file:
            preset_file.write(b"preset")

        self.client.force_login(self.member)
        with override_settings(STATICFILES_DIRS=[self.static_dir], MEDIA_ROOT=self.media_dir):
            response = self.client.post(reverse("photo_upload_group", args=[self.group.id]), {"preset": preset_path})

        self.assertEqual(response.status_code, 200)
        self.group.refresh_from_db()
        self.assertFalse(self.group.image)
        self.assertEqual(self.group.image_preset, preset_path)
        self.assertEqual(os.listdir(self.media_dir), [])

    def test_upload_group_image_clears_preset(self):
        """Uploading a custom group image takes priority over a previous preset."""
        self.group.image_preset = "gifts/presets/group/family.jpg"
        self.group.save(update_fields=["image_preset"])

        self.client.force_login(self.member)
        with override_settings(MEDIA_ROOT=self.media_dir):
            response = self.client.post(reverse("photo_upload_group", args=[self.group.id]), {"photo": make_image()})

        self.assertEqual(response.status_code, 200)
        self.group.refresh_from_db()
        self.assertTrue(self.group.image)
        self.assertEqual(self.group.image_preset, "")


class AvatarUploadTest(TestCase):
    def setUp(self):
        self.user, self.other, _ = create_users()
        self.media_dir = tempfile.mkdtemp()
        self.static_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.media_dir, ignore_errors=True)
        shutil.rmtree(self.static_dir, ignore_errors=True)

    def test_upload_avatar(self):
        """User can upload an avatar from the account page."""
        self.client.force_login(self.user)
        with override_settings(MEDIA_ROOT=self.media_dir):
            response = self.client.post(
                reverse("account"),
                {"nickname": self.user.nickname, "email": self.user.email, "avatar": make_image()},
            )
        self.assertRedirects(response, reverse("account"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.avatar)
        self.assertIn(f"profiles/{self.user.id}/", self.user.avatar.name)

    def test_avatar_filename_uses_uuid(self):
        """The stored filename is a UUID, not the original upload name."""
        self.client.force_login(self.user)
        with override_settings(MEDIA_ROOT=self.media_dir):
            self.client.post(
                reverse("account"),
                {"nickname": self.user.nickname, "email": self.user.email, "avatar": make_image("my_photo.jpg")},
            )
        self.user.refresh_from_db()
        filename = os.path.basename(self.user.avatar.name)
        self.assertNotEqual(filename, "my_photo.jpg")

    def test_replace_avatar_removes_old_file(self):
        """Uploading a new avatar deletes the previous file from disk."""
        self.client.force_login(self.user)
        with override_settings(MEDIA_ROOT=self.media_dir):
            self.client.post(
                reverse("account"),
                {"nickname": self.user.nickname, "email": self.user.email, "avatar": make_image("first.jpg")},
            )
            self.user.refresh_from_db()
            old_path = self.user.avatar.path

            self.client.post(
                reverse("account"),
                {"nickname": self.user.nickname, "email": self.user.email, "avatar": make_image("second.jpg")},
            )
            self.assertFalse(os.path.isfile(old_path))

        self.user.refresh_from_db()
        self.assertTrue(self.user.avatar)

    def test_update_profile_without_avatar_keeps_existing(self):
        """Updating nickname/email without a new image preserves the existing avatar."""
        self.client.force_login(self.user)
        with override_settings(MEDIA_ROOT=self.media_dir):
            self.client.post(
                reverse("account"),
                {"nickname": self.user.nickname, "email": self.user.email, "avatar": make_image()},
            )
            self.user.refresh_from_db()
            old_avatar_name = self.user.avatar.name

            self.client.post(
                reverse("account"),
                {"nickname": "newnick", "email": self.user.email},
            )

        self.user.refresh_from_db()
        self.assertEqual(self.user.avatar.name, old_avatar_name)
        self.assertEqual(self.user.nickname, "newnick")

    def test_unauthenticated_cannot_upload_avatar(self):
        """Anonymous users are redirected to login."""
        with override_settings(MEDIA_ROOT=self.media_dir):
            response = self.client.post(
                reverse("account"),
                {"nickname": "hacker", "email": "hacker@test.com", "avatar": make_image()},
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_avatar_image_is_resized(self):
        """Uploaded avatar is resized to 200x200."""
        self.client.force_login(self.user)
        with override_settings(MEDIA_ROOT=self.media_dir):
            self.client.post(
                reverse("account"),
                {"nickname": self.user.nickname, "email": self.user.email, "avatar": make_image(width=800, height=600)},
            )
            self.user.refresh_from_db()
            from PIL import Image as PILImage

            with PILImage.open(self.user.avatar.path) as img:
                self.assertEqual(img.size, (200, 200))

    def test_choose_avatar_preset_stores_reference_without_copy(self):
        """Choosing a preset stores its static path instead of copying a media file."""
        preset_path = "gifts/presets/profile/smile.png"
        os.makedirs(os.path.join(self.static_dir, "gifts", "presets", "profile"), exist_ok=True)
        with open(os.path.join(self.static_dir, preset_path), "wb") as preset_file:
            preset_file.write(b"preset")

        self.client.force_login(self.user)
        with override_settings(STATICFILES_DIRS=[self.static_dir], MEDIA_ROOT=self.media_dir):
            response = self.client.post(reverse("photo_upload_profile"), {"preset": preset_path})

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.avatar)
        self.assertEqual(self.user.avatar_preset, preset_path)
        self.assertEqual(os.listdir(self.media_dir), [])

    def test_upload_avatar_clears_preset(self):
        """Uploading a custom avatar takes priority over a previous preset."""
        self.user.avatar_preset = "gifts/presets/profile/smile.png"
        self.user.save(update_fields=["avatar_preset"])

        self.client.force_login(self.user)
        with override_settings(MEDIA_ROOT=self.media_dir):
            response = self.client.post(reverse("photo_upload_profile"), {"photo": make_image()})

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.avatar)
        self.assertEqual(self.user.avatar_preset, "")


class OfferGiftTest(TestCase):
    """Tests for the offer_gift view — covers Bug 1 (modal not opening) and Bug 2 (no real-time split)."""

    def setUp(self):
        self.user1, self.user2, self.user3 = create_users()
        self.group = Group.objects.create(name="Test Group", show_history=True)
        self.group.members.add(self.user1, self.user2, self.user3)
        self.gift = Gift.objects.create(owner=self.user1, created_by=self.user1, title="My Gift")

    def _post_offer(self, user, data):
        self.client.force_login(user)
        return self.client.post(
            reverse("offer_gift", args=[self.gift.id]),
            data=json.dumps(data),
            content_type="application/json",
        )

    def test_offer_modal_returned_for_non_owner(self):
        """Bug 1: POST to offer_gift (as JS offerFromList does) returns 200 with HTML modal."""
        response = self._post_offer(self.user2, {"group_id": self.group.id})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "offerTotalCost")

    def test_offer_modal_has_givers_when_group_provided(self):
        """Bug 2: When group_id is sent, modal renders split checkboxes and per-person span."""
        response = self._post_offer(self.user2, {"group_id": self.group.id})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "split-checkbox")
        self.assertContains(response, "offerSharePerPerson")

    def test_offer_modal_no_givers_without_group(self):
        """Bug 2: Without group_id, givers list is empty so split section is absent."""
        response = self._post_offer(self.user2, {})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "split-checkbox")
        self.assertNotContains(response, "offerSharePerPerson")

    def test_owner_cannot_offer_own_gift(self):
        response = self._post_offer(self.user1, {"group_id": self.group.id})
        self.assertEqual(response.status_code, 403)

    def test_offer_confirm_marks_gift_offered_with_cost_and_split(self):
        """Confirming offer marks gift offered, records actual_cost, payers, and expense_split."""
        response = self._post_offer(
            self.user2,
            {
                "confirm": True,
                "group_id": self.group.id,
                "actual_cost": "60.00",
                "payers": {str(self.user2.id): "60.00"},
                "split_participants": [self.user2.id, self.user3.id],
            },
        )
        data = json.loads(response.content)
        self.assertTrue(data["success"])
        self.gift.refresh_from_db()
        self.assertTrue(self.gift.offered)
        self.assertEqual(self.gift.actual_cost, Decimal("60.00"))
        split_ids = set(self.gift.expense_split.values_list("id", flat=True))
        self.assertIn(self.user2.id, split_ids)
        self.assertIn(self.user3.id, split_ids)
        reservation = Reservation.objects.get(gift=self.gift, reserver=self.user2)
        self.assertEqual(reservation.amount_paid, Decimal("60.00"))

    def test_offer_confirm_history_disabled_deletes_gift(self):
        """When history is disabled for the group, confirming offer deletes the gift permanently."""
        self.group.show_history = False
        self.group.save()
        self.gift.group_reserved_on = self.group
        self.gift.save()

        response = self._post_offer(self.user2, {"confirm": True, "group_id": self.group.id})
        data = json.loads(response.content)
        self.assertTrue(data["success"])
        self.assertFalse(Gift.objects.filter(id=self.gift.id).exists())

    def test_offer_confirm_skip_split_marks_offered_without_cost(self):
        """Skip-split variant (no payers/split) marks gift offered with no cost tracking."""
        response = self._post_offer(self.user2, {"confirm": True, "group_id": self.group.id})
        data = json.loads(response.content)
        self.assertTrue(data["success"])
        self.gift.refresh_from_db()
        self.assertTrue(self.gift.offered)
        self.assertIsNone(self.gift.actual_cost)

    def test_offer_confirm_excludes_owner_from_split(self):
        """Gift owner is excluded from expense_split even if included in split_participants."""
        response = self._post_offer(
            self.user2,
            {
                "confirm": True,
                "group_id": self.group.id,
                "actual_cost": "30.00",
                "payers": {str(self.user2.id): "30.00"},
                "split_participants": [self.user1.id, self.user2.id],  # user1 is the owner
            },
        )
        self.assertTrue(json.loads(response.content)["success"])
        self.gift.refresh_from_db()
        split_ids = set(self.gift.expense_split.values_list("id", flat=True))
        self.assertNotIn(self.user1.id, split_ids)  # owner excluded
        self.assertIn(self.user2.id, split_ids)


class ViewListJsTranslationTest(TestCase):
    """Bug: {% trans %} strings with apostrophes inside JS single-quoted literals
    break the entire <script> block in French, making offerFromList undefined."""

    def setUp(self):
        self.user1, self.user2, _ = create_users()
        self.group = Group.objects.create(name="Test Group")
        self.group.members.add(self.user1, self.user2)

    def test_offer_from_list_js_not_broken_in_french(self):
        activate("fr")
        try:
            french_err = _("An error occurred.")
            self.client.force_login(self.user2)
            response = self.client.get(
                reverse("view_list", args=[self.user1.id]),
                {"from_group": str(self.group.id)},
            )
        finally:
            deactivate()

        self.assertEqual(response.status_code, 200)
        self.assertIn("'", french_err, "precondition: French translation must contain an apostrophe")
        content = response.content.decode()
        # The raw French string must NOT appear inside a JS single-quoted string literal.
        # If it does, the whole <script> block fails to parse and offerFromList is never defined.
        self.assertNotIn(f"|| '{french_err}'", content)


class HistoryViewTest(TestCase):
    def setUp(self):
        self.user1, self.user2, self.outsider = create_users()
        self.group = Group.objects.create(name="History Group", show_history=True)
        self.group.members.add(self.user1, self.user2)

    def test_non_member_access_denied(self):
        self.client.force_login(self.outsider)
        response = self.client.get(reverse("history_group", args=[self.group.id]))
        self.assertEqual(response.status_code, 403)

    def test_no_group_id_redirects_to_dashboard(self):
        self.client.force_login(self.user1)
        response = self.client.get(reverse("history"))
        self.assertRedirects(response, reverse("dashboard"))

    def test_history_disabled_shows_empty_list_and_flag(self):
        self.group.show_history = False
        self.group.save()
        self.client.force_login(self.user1)
        response = self.client.get(reverse("history_group", args=[self.group.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["history_disabled"])
        self.assertEqual(response.context["gifts"], [])

    def test_history_enabled_shows_offered_gifts(self):
        gift = Gift.objects.create(
            owner=self.user1,
            created_by=self.user2,
            title="Offered Gift",
            offered=True,
            offered_at=timezone.now(),
            group_reserved_on=self.group,
        )
        self.client.force_login(self.user1)
        response = self.client.get(reverse("history_group", args=[self.group.id]))
        self.assertEqual(response.status_code, 200)
        self.assertIn(gift, response.context["gifts"])

    def test_history_does_not_show_non_offered_gifts(self):
        Gift.objects.create(owner=self.user1, created_by=self.user1, title="Still Wanted")
        self.client.force_login(self.user1)
        response = self.client.get(reverse("history_group", args=[self.group.id]))
        self.assertEqual(len(response.context["gifts"]), 0)

    def test_history_marks_current_user_reserved_gifts(self):
        gift = Gift.objects.create(
            owner=self.user1,
            created_by=self.user2,
            title="Reserved Gift",
            offered=True,
            offered_at=timezone.now(),
            group_reserved_on=self.group,
        )
        Reservation.objects.create(gift=gift, reserver=self.user2)
        self.client.force_login(self.user2)
        response = self.client.get(reverse("history_group", args=[self.group.id]))
        self.assertIn(gift.id, response.context["user_reserved_ids"])

    def test_history_does_not_mark_other_users_reservations(self):
        gift = Gift.objects.create(
            owner=self.user1,
            created_by=self.user2,
            title="Reserved by Other",
            offered=True,
            offered_at=timezone.now(),
            group_reserved_on=self.group,
        )
        Reservation.objects.create(gift=gift, reserver=self.user2)
        self.client.force_login(self.user1)  # user1 did not reserve
        response = self.client.get(reverse("history_group", args=[self.group.id]))
        self.assertNotIn(gift.id, response.context["user_reserved_ids"])


class ComputeGroupBalancesTest(TestCase):
    """Unit tests for the compute_group_balances pure function."""

    def setUp(self):
        self.user1, self.user2, self.user3 = create_users()
        self.group = Group.objects.create(name="Balance Group", show_history=True, show_balance=True)
        self.group.members.add(self.user1, self.user2, self.user3)

    def test_empty_group_returns_empty(self):
        balances, transactions, _ = compute_group_balances(self.group)
        self.assertEqual(balances, {})
        self.assertEqual(transactions, [])

    def test_single_gift_equal_split_two_members(self):
        """user2 pays 60 for a gift split equally with user3 → each owes 30, user2 net +30."""
        gift = Gift.objects.create(
            owner=self.user1,
            created_by=self.user2,
            title="Gift",
            offered=True,
            offered_at=timezone.now(),
            group_reserved_on=self.group,
            actual_cost=Decimal("60.00"),
        )
        gift.expense_split.set([self.user2, self.user3])
        Reservation.objects.create(gift=gift, reserver=self.user2, amount_paid=Decimal("60.00"))

        balances, transactions, _ = compute_group_balances(self.group)
        self.assertEqual(balances[self.user2], Decimal("30.00"))
        self.assertEqual(balances[self.user3], Decimal("-30.00"))
        self.assertEqual(len(transactions), 1)
        debtor, creditor, amount = transactions[0]
        self.assertEqual(debtor, self.user3)
        self.assertEqual(creditor, self.user2)
        self.assertEqual(amount, Decimal("30.00"))

    def test_settlement_clears_debt(self):
        """After recording a settlement, the transaction disappears."""
        gift = Gift.objects.create(
            owner=self.user1,
            created_by=self.user2,
            title="Gift",
            offered=True,
            offered_at=timezone.now(),
            group_reserved_on=self.group,
            actual_cost=Decimal("60.00"),
        )
        gift.expense_split.set([self.user2, self.user3])
        Reservation.objects.create(gift=gift, reserver=self.user2, amount_paid=Decimal("60.00"))
        BalanceSettlement.objects.create(group=self.group, payer=self.user3, payee=self.user2, amount=Decimal("30.00"))

        _, transactions, _ = compute_group_balances(self.group)
        self.assertEqual(transactions, [])

    def test_gift_without_expense_split_is_ignored(self):
        """Gifts with no expense_split entries do not affect balances."""
        gift = Gift.objects.create(
            owner=self.user1,
            created_by=self.user2,
            title="No Split Gift",
            offered=True,
            offered_at=timezone.now(),
            group_reserved_on=self.group,
            actual_cost=Decimal("50.00"),
        )
        Reservation.objects.create(gift=gift, reserver=self.user2, amount_paid=Decimal("50.00"))
        # expense_split intentionally left empty

        balances, transactions, _ = compute_group_balances(self.group)
        self.assertEqual(balances, {})
        self.assertEqual(transactions, [])

    def test_gift_with_zero_cost_is_ignored(self):
        gift = Gift.objects.create(
            owner=self.user1,
            created_by=self.user2,
            title="Free Gift",
            offered=True,
            offered_at=timezone.now(),
            group_reserved_on=self.group,
            actual_cost=Decimal("0.00"),
        )
        gift.expense_split.set([self.user2, self.user3])

        balances, _, _ = compute_group_balances(self.group)
        self.assertEqual(balances, {})


class BalanceViewTest(TestCase):
    def setUp(self):
        self.user1, self.user2, self.outsider = create_users()
        self.group = Group.objects.create(name="Balance Group", show_balance=True)
        self.group.members.add(self.user1, self.user2)

    def test_non_member_access_denied(self):
        self.client.force_login(self.outsider)
        response = self.client.get(reverse("balance_group", args=[self.group.id]))
        self.assertEqual(response.status_code, 403)

    def test_balance_disabled_shows_flag(self):
        self.group.show_balance = False
        self.group.save()
        self.client.force_login(self.user1)
        response = self.client.get(reverse("balance_group", args=[self.group.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["balance_disabled"])

    def test_balance_enabled_shows_transactions(self):
        """user2 pays 40 for a gift split with user1 → user1 owes user2 20."""
        gift = Gift.objects.create(
            owner=self.outsider,
            created_by=self.user2,
            title="Gift",
            offered=True,
            offered_at=timezone.now(),
            group_reserved_on=self.group,
            actual_cost=Decimal("40.00"),
        )
        gift.expense_split.set([self.user1, self.user2])
        Reservation.objects.create(gift=gift, reserver=self.user2, amount_paid=Decimal("40.00"))

        self.client.force_login(self.user1)
        response = self.client.get(reverse("balance_group", args=[self.group.id]))
        self.assertEqual(response.status_code, 200)
        transactions = response.context["transactions"]
        self.assertEqual(len(transactions), 1)
        debtor, creditor, amount = transactions[0]
        self.assertEqual(debtor, self.user1)
        self.assertEqual(creditor, self.user2)
        self.assertEqual(amount, Decimal("20.00"))

    def test_add_settlement_creates_record(self):
        self.client.force_login(self.user1)
        response = self.client.post(
            reverse("add_settlement", args=[self.group.id]),
            {"payee_id": self.user2.id, "amount": "25.00"},
        )
        self.assertRedirects(response, reverse("balance_group", args=[self.group.id]))
        self.assertTrue(
            BalanceSettlement.objects.filter(
                group=self.group, payer=self.user1, payee=self.user2, amount=Decimal("25.00")
            ).exists()
        )

    def test_add_settlement_negative_amount_rejected(self):
        self.client.force_login(self.user1)
        self.client.post(
            reverse("add_settlement", args=[self.group.id]),
            {"payee_id": self.user2.id, "amount": "-10.00"},
        )
        self.assertFalse(BalanceSettlement.objects.filter(group=self.group).exists())

    def test_add_settlement_non_member_forbidden(self):
        self.client.force_login(self.outsider)
        response = self.client.post(
            reverse("add_settlement", args=[self.group.id]),
            {"payee_id": self.user2.id, "amount": "10.00"},
        )
        self.assertEqual(response.status_code, 403)


class EditOfferedAmountsTest(TestCase):
    def setUp(self):
        self.user1, self.user2, self.user3 = create_users()
        self.group = Group.objects.create(name="Group", show_history=True)
        self.group.members.add(self.user1, self.user2, self.user3)
        self.gift = Gift.objects.create(
            owner=self.user1,
            created_by=self.user2,
            title="Offered Gift",
            offered=True,
            offered_at=timezone.now(),
            group_reserved_on=self.group,
            actual_cost=Decimal("30.00"),
        )
        Reservation.objects.create(gift=self.gift, reserver=self.user2, amount_paid=Decimal("30.00"))

    def _post(self, user, data):
        self.client.force_login(user)
        return self.client.post(
            reverse("edit_offered_amounts", args=[self.gift.id]),
            data=json.dumps(data),
            content_type="application/json",
        )

    def test_get_modal_returns_html_with_cost_field(self):
        response = self._post(self.user2, {})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "editTotalCost")

    def test_get_modal_includes_split_fields_for_group_gift(self):
        """Bug 2 equivalent for edit modal: split section present when gift has a group."""
        response = self._post(self.user2, {})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "edit-split-checkbox")
        self.assertContains(response, "editSharePerPerson")

    def test_save_updates_actual_cost_and_split(self):
        response = self._post(
            self.user2,
            {
                "save": True,
                "actual_cost": "55.00",
                "payers": {str(self.user2.id): "55.00"},
                "split_participants": [self.user2.id, self.user3.id],
            },
        )
        self.assertTrue(json.loads(response.content)["success"])
        self.gift.refresh_from_db()
        self.assertEqual(self.gift.actual_cost, Decimal("55.00"))
        split_ids = set(self.gift.expense_split.values_list("id", flat=True))
        self.assertIn(self.user2.id, split_ids)
        self.assertIn(self.user3.id, split_ids)

    def test_save_empty_cost_clears_actual_cost(self):
        response = self._post(
            self.user2,
            {
                "save": True,
                "actual_cost": "",
                "payers": {},
                "split_participants": [],
            },
        )
        self.assertTrue(json.loads(response.content)["success"])
        self.gift.refresh_from_db()
        self.assertIsNone(self.gift.actual_cost)
        self.assertEqual(self.gift.expense_split.count(), 0)

    def test_non_reserver_non_owner_forbidden(self):
        """user3 is a group member but not owner or reserver → 403."""
        response = self._post(self.user3, {})
        self.assertEqual(response.status_code, 403)

    def test_owner_can_get_modal(self):
        response = self._post(self.user1, {})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "editTotalCost")


class UnOfferDeleteGiftTest(TestCase):
    def setUp(self):
        self.user1, self.user2, self.user3 = create_users()
        self.group = Group.objects.create(name="Group", show_history=True)
        self.group.members.add(self.user1, self.user2, self.user3)
        self.gift = Gift.objects.create(
            owner=self.user1,
            created_by=self.user2,
            title="Offered Gift",
            offered=True,
            offered_at=timezone.now(),
            group_reserved_on=self.group,
            actual_cost=Decimal("30.00"),
        )
        Reservation.objects.create(gift=self.gift, reserver=self.user2, amount_paid=Decimal("30.00"))
        self.gift.expense_split.set([self.user2])

    def test_reserver_can_un_offer(self):
        """Reserver can put the gift back in the list, resetting all financial data."""
        self.client.force_login(self.user2)
        response = self.client.post(reverse("un_offer_gift", args=[self.gift.id]))
        self.assertRedirects(response, reverse("history_group", args=[self.group.id]))
        self.gift.refresh_from_db()
        self.assertFalse(self.gift.offered)
        self.assertIsNone(self.gift.actual_cost)
        self.assertIsNone(self.gift.offered_at)
        self.assertEqual(self.gift.expense_split.count(), 0)
        res = Reservation.objects.get(gift=self.gift, reserver=self.user2)
        self.assertIsNone(res.amount_paid)

    def test_owner_can_un_offer(self):
        self.client.force_login(self.user1)
        self.client.post(reverse("un_offer_gift", args=[self.gift.id]))
        self.gift.refresh_from_db()
        self.assertFalse(self.gift.offered)

    def test_group_member_can_un_offer(self):
        """A plain group member (not owner or reserver) can also un-offer."""
        self.client.force_login(self.user3)
        response = self.client.post(reverse("un_offer_gift", args=[self.gift.id]))
        self.assertIn(response.status_code, [302])
        self.gift.refresh_from_db()
        self.assertFalse(self.gift.offered)

    def test_outsider_cannot_un_offer(self):
        """A user with no relation to the gift/group cannot un-offer it."""
        self.group.members.remove(self.user3)
        self.client.force_login(self.user3)
        response = self.client.post(reverse("un_offer_gift", args=[self.gift.id]))
        self.assertEqual(response.status_code, 403)
        self.gift.refresh_from_db()
        self.assertTrue(self.gift.offered)

    def test_reserver_can_delete_offered_gift(self):
        self.client.force_login(self.user2)
        response = self.client.post(reverse("delete_offered_gift", args=[self.gift.id]))
        self.assertRedirects(response, reverse("history_group", args=[self.group.id]))
        self.assertFalse(Gift.objects.filter(id=self.gift.id).exists())

    def test_owner_can_delete_offered_gift(self):
        self.client.force_login(self.user1)
        response = self.client.post(reverse("delete_offered_gift", args=[self.gift.id]))
        self.assertRedirects(response, reverse("history_group", args=[self.group.id]))
        self.assertFalse(Gift.objects.filter(id=self.gift.id).exists())

    def test_non_owner_non_reserver_cannot_delete(self):
        self.client.force_login(self.user3)
        response = self.client.post(reverse("delete_offered_gift", args=[self.gift.id]))
        self.assertEqual(response.status_code, 403)


# ── Event List tests ──────────────────────────────────────────────────────────


class EventListCRUDTest(TestCase):
    def setUp(self):
        self.user1, self.user2, self.user3 = create_users()
        self.event = EventList.objects.create(name="Wedding", owner=self.user1)

    def test_create_event_list_redirects_to_detail(self):
        self.client.force_login(self.user1)
        response = self.client.post(
            reverse("create_event_list"),
            {"name": "Birthday", "description": "", "event_date": ""},
        )
        event = EventList.objects.get(name="Birthday")
        self.assertRedirects(response, reverse("event_detail", kwargs={"token": event.access_token}))

    def test_create_event_list_empty_name_redirects_to_dashboard(self):
        self.client.force_login(self.user1)
        response = self.client.post(reverse("create_event_list"), {"name": "", "description": ""})
        self.assertRedirects(response, reverse("dashboard"))
        self.assertEqual(EventList.objects.filter(owner=self.user1).count(), 1)

    def test_create_requires_login(self):
        response = self.client.post(reverse("create_event_list"), {"name": "Test"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_edit_info_updates_name_desc_date(self):
        self.client.force_login(self.user1)
        response = self.client.post(
            reverse("edit_event_info", kwargs={"token": self.event.access_token}),
            {"name": "New Name", "description": "A description", "event_date": "2026-12-25"},
        )
        self.assertRedirects(response, reverse("event_detail", kwargs={"token": self.event.access_token}))
        self.event.refresh_from_db()
        self.assertEqual(self.event.name, "New Name")
        self.assertEqual(self.event.description, "A description")
        self.assertEqual(str(self.event.event_date), "2026-12-25")

    def test_edit_info_clears_date_when_empty(self):
        import datetime

        self.event.event_date = datetime.date(2026, 1, 1)
        self.event.save()
        self.client.force_login(self.user1)
        self.client.post(
            reverse("edit_event_info", kwargs={"token": self.event.access_token}),
            {"name": "Wedding", "description": "", "event_date": ""},
        )
        self.event.refresh_from_db()
        self.assertIsNone(self.event.event_date)

    def test_edit_info_forbidden_for_non_owner(self):
        self.client.force_login(self.user2)
        response = self.client.post(
            reverse("edit_event_info", kwargs={"token": self.event.access_token}),
            {"name": "Hack"},
        )
        self.assertEqual(response.status_code, 403)

    def test_delete_event_list_by_owner(self):
        self.client.force_login(self.user1)
        token = self.event.access_token
        response = self.client.post(reverse("delete_event_list", kwargs={"token": token}))
        self.assertRedirects(response, reverse("dashboard"))
        self.assertFalse(EventList.objects.filter(access_token=token).exists())

    def test_delete_forbidden_for_non_owner(self):
        self.client.force_login(self.user2)
        response = self.client.post(reverse("delete_event_list", kwargs={"token": self.event.access_token}))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(EventList.objects.filter(id=self.event.id).exists())


class EventDetailViewTest(TestCase):
    def setUp(self):
        self.user1, self.user2, self.user3 = create_users()
        self.event = EventList.objects.create(name="Party", owner=self.user1)
        self.visible_gift = Gift.objects.create(
            owner=self.user1, created_by=self.user1, title="Visible", event_list=self.event
        )
        self.hidden_gift = Gift.objects.create(
            owner=self.user1, created_by=self.user1, title="Hidden", event_list=self.event, is_hidden=True
        )

    def _url(self):
        return reverse("event_detail", kwargs={"token": self.event.access_token})

    def test_owner_sees_hidden_gifts(self):
        self.client.force_login(self.user1)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["gifts_list"]), 2)

    def test_visitor_does_not_see_hidden_gifts(self):
        self.client.force_login(self.user2)
        response = self.client.get(self._url())
        self.assertEqual(len(response.context["gifts_list"]), 1)
        self.assertEqual(response.context["gifts_list"][0]["gift"].title, "Visible")

    def test_anonymous_does_not_see_hidden_gifts(self):
        response = self.client.get(self._url())
        self.assertEqual(len(response.context["gifts_list"]), 1)

    def test_authenticated_visitor_added_to_participants(self):
        self.client.force_login(self.user2)
        self.client.get(self._url())
        self.assertIn(self.user2, self.event.participants.all())

    def test_owner_not_added_to_participants(self):
        self.client.force_login(self.user1)
        self.client.get(self._url())
        self.assertNotIn(self.user1, self.event.participants.all())

    def test_anonymous_not_added_to_participants(self):
        self.client.get(self._url())
        self.assertEqual(self.event.participants.count(), 0)

    def test_is_owner_true_for_owner(self):
        self.client.force_login(self.user1)
        response = self.client.get(self._url())
        self.assertTrue(response.context["is_owner"])

    def test_is_owner_false_for_visitor(self):
        self.client.force_login(self.user2)
        response = self.client.get(self._url())
        self.assertFalse(response.context["is_owner"])

    def test_invalid_token_returns_404(self):
        response = self.client.get(reverse("event_detail", kwargs={"token": "INVALID99"}))
        self.assertEqual(response.status_code, 404)


class SecretSantaEventTest(TestCase):
    def setUp(self):
        self.user1, self.user2, self.user3 = create_users()
        self.event = EventList.objects.create(
            name="Christmas",
            owner=self.user1,
            mode=EventList.MODE_SECRET_SANTA,
            budget_max=Decimal("30.00"),
        )
        self.event.participants.add(self.user2, self.user3)

    def test_create_secret_santa_event_with_budget(self):
        self.client.force_login(self.user1)
        response = self.client.post(
            reverse("create_event_list"),
            {
                "name": "Family Christmas",
                "mode": "secret_santa",
                "budget_max": "45,50",
                "event_date": "2026-12-25",
            },
        )
        event = EventList.objects.get(name="Family Christmas")
        self.assertRedirects(response, reverse("event_detail", kwargs={"token": event.access_token}))
        self.assertEqual(event.mode, EventList.MODE_SECRET_SANTA)
        self.assertEqual(event.budget_max, Decimal("45.50"))

    def test_owner_can_add_bidirectional_exclusion(self):
        self.client.force_login(self.user1)
        response = self.client.post(
            reverse("add_secret_santa_exclusion", kwargs={"token": self.event.access_token}),
            {"giver": self.user1.id, "receiver": self.user2.id, "both_directions": "1"},
        )
        self.assertRedirects(response, reverse("event_detail", kwargs={"token": self.event.access_token}))
        self.assertTrue(
            SecretSantaExclusion.objects.filter(event=self.event, giver=self.user1, receiver=self.user2).exists()
        )
        self.assertTrue(
            SecretSantaExclusion.objects.filter(event=self.event, giver=self.user2, receiver=self.user1).exists()
        )

    def test_non_owner_cannot_add_exclusion(self):
        self.client.force_login(self.user2)
        response = self.client.post(
            reverse("add_secret_santa_exclusion", kwargs={"token": self.event.access_token}),
            {"giver": self.user1.id, "receiver": self.user2.id},
        )
        self.assertEqual(response.status_code, 403)

    def test_draw_creates_one_assignment_per_participant(self):
        SecretSantaExclusion.objects.create(event=self.event, giver=self.user1, receiver=self.user2)
        self.client.force_login(self.user1)
        response = self.client.post(reverse("draw_secret_santa", kwargs={"token": self.event.access_token}))
        self.assertRedirects(response, reverse("event_detail", kwargs={"token": self.event.access_token}))

        assignments = list(SecretSantaAssignment.objects.filter(event=self.event))
        self.assertEqual(len(assignments), 3)
        self.assertEqual(
            {assignment.giver_id for assignment in assignments}, {self.user1.id, self.user2.id, self.user3.id}
        )
        self.assertEqual(
            {assignment.receiver_id for assignment in assignments}, {self.user1.id, self.user2.id, self.user3.id}
        )
        self.assertFalse(
            SecretSantaAssignment.objects.filter(event=self.event, giver=self.user1, receiver=self.user2).exists()
        )
        self.assertFalse(any(assignment.giver_id == assignment.receiver_id for assignment in assignments))

    def test_draw_fails_when_constraints_are_impossible(self):
        self.event.participants.remove(self.user3)
        SecretSantaExclusion.objects.create(event=self.event, giver=self.user1, receiver=self.user2)
        self.client.force_login(self.user1)
        response = self.client.post(reverse("draw_secret_santa", kwargs={"token": self.event.access_token}))
        self.assertRedirects(response, reverse("event_detail", kwargs={"token": self.event.access_token}))
        self.assertFalse(SecretSantaAssignment.objects.filter(event=self.event).exists())

    def test_assignment_allows_receiver_wish_list_access_without_common_group(self):
        Gift.objects.create(owner=self.user2, created_by=self.user2, title="Book")
        SecretSantaAssignment.objects.create(event=self.event, giver=self.user1, receiver=self.user2)
        self.client.force_login(self.user1)
        response = self.client.get(reverse("view_list", args=[self.user2.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Book")

    def test_owner_can_add_guest_participant_with_default_owner_exclusion(self):
        self.client.force_login(self.user1)
        response = self.client.post(
            reverse("add_secret_santa_guest_participant", kwargs={"token": self.event.access_token}),
            {"name": "Grandma"},
        )
        self.assertRedirects(response, reverse("event_detail", kwargs={"token": self.event.access_token}))
        guest = SecretSantaGuestParticipant.objects.get(event=self.event, name="Grandma")
        self.assertTrue(
            SecretSantaExclusion.objects.filter(event=self.event, giver_guest=guest, receiver=self.user1).exists()
        )

    def test_guest_participant_is_included_in_draw_and_cannot_draw_owner_by_default(self):
        guest = SecretSantaGuestParticipant.objects.create(event=self.event, name="Grandma")
        SecretSantaExclusion.objects.create(event=self.event, giver_guest=guest, receiver=self.user1)
        self.client.force_login(self.user1)
        response = self.client.post(reverse("draw_secret_santa", kwargs={"token": self.event.access_token}))
        self.assertRedirects(response, reverse("event_detail", kwargs={"token": self.event.access_token}))

        guest_assignment = SecretSantaAssignment.objects.get(event=self.event, giver_guest=guest)
        self.assertNotEqual(guest_assignment.receiver, self.user1)
        self.assertEqual(SecretSantaAssignment.objects.filter(event=self.event).count(), 4)

    def test_owner_context_includes_guest_assignments_only(self):
        guest = SecretSantaGuestParticipant.objects.create(event=self.event, name="Grandma")
        SecretSantaAssignment.objects.create(event=self.event, giver_guest=guest, receiver=self.user2)
        SecretSantaAssignment.objects.create(event=self.event, giver=self.user1, receiver=self.user3)
        self.client.force_login(self.user1)
        response = self.client.get(reverse("event_detail", kwargs={"token": self.event.access_token}))
        self.assertEqual(
            list(response.context["secret_santa_guest_assignments"]),
            [guest.secret_santa_assignments_as_giver.get()],
        )


class EventGiftManagementTest(TestCase):
    def setUp(self):
        self.user1, self.user2, self.user3 = create_users()
        self.event = EventList.objects.create(name="Shower", owner=self.user1)
        self.gift = Gift.objects.create(
            owner=self.user1, created_by=self.user1, title="Existing", event_list=self.event
        )

    def _post_json(self, url_name, data, user=None, **kwargs):
        if user:
            self.client.force_login(user)
        return self.client.post(
            reverse(url_name, kwargs={"token": self.event.access_token, **kwargs}),
            data=json.dumps(data),
            content_type="application/json",
        )

    def test_add_gift_success(self):
        response = self._post_json("add_event_gift", {"title": "New Gift", "price": "25.00"}, user=self.user1)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data["success"])
        self.assertTrue(Gift.objects.filter(title="New Gift", event_list=self.event).exists())

    def test_add_gift_missing_title(self):
        response = self._post_json("add_event_gift", {}, user=self.user1)
        self.assertEqual(response.status_code, 400)

    def test_add_gift_forbidden_for_non_owner(self):
        response = self._post_json("add_event_gift", {"title": "Hack"}, user=self.user2)
        self.assertEqual(response.status_code, 403)

    def test_add_gift_requires_login(self):
        response = self.client.post(
            reverse("add_event_gift", kwargs={"token": self.event.access_token}),
            data=json.dumps({"title": "Test"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_edit_gift_success(self):
        response = self._post_json("edit_event_gift", {"title": "Updated"}, user=self.user1, gift_id=self.gift.id)
        self.assertEqual(response.status_code, 200)
        self.gift.refresh_from_db()
        self.assertEqual(self.gift.title, "Updated")

    def test_edit_gift_forbidden_for_non_owner(self):
        response = self._post_json("edit_event_gift", {"title": "Hack"}, user=self.user2, gift_id=self.gift.id)
        self.assertEqual(response.status_code, 403)

    def test_delete_gift_success(self):
        self.client.force_login(self.user1)
        response = self.client.post(
            reverse("delete_event_gift", kwargs={"token": self.event.access_token, "gift_id": self.gift.id})
        )
        self.assertRedirects(response, reverse("event_detail", kwargs={"token": self.event.access_token}))
        self.assertFalse(Gift.objects.filter(id=self.gift.id).exists())

    def test_delete_gift_forbidden_for_non_owner(self):
        self.client.force_login(self.user2)
        response = self.client.post(
            reverse("delete_event_gift", kwargs={"token": self.event.access_token, "gift_id": self.gift.id})
        )
        self.assertEqual(response.status_code, 403)

    def test_toggle_hidden_hides_gift(self):
        self.client.force_login(self.user1)
        response = self.client.post(
            reverse("toggle_event_gift_hidden", kwargs={"token": self.event.access_token, "gift_id": self.gift.id})
        )
        data = json.loads(response.content)
        self.assertTrue(data["hidden"])
        self.gift.refresh_from_db()
        self.assertTrue(self.gift.is_hidden)

    def test_toggle_hidden_shows_gift(self):
        self.gift.is_hidden = True
        self.gift.save()
        self.client.force_login(self.user1)
        response = self.client.post(
            reverse("toggle_event_gift_hidden", kwargs={"token": self.event.access_token, "gift_id": self.gift.id})
        )
        data = json.loads(response.content)
        self.assertFalse(data["hidden"])

    def test_toggle_hidden_forbidden_for_non_owner(self):
        self.client.force_login(self.user2)
        response = self.client.post(
            reverse("toggle_event_gift_hidden", kwargs={"token": self.event.access_token, "gift_id": self.gift.id})
        )
        self.assertEqual(response.status_code, 403)


class GuestReservationTest(TestCase):
    def setUp(self):
        self.user1, self.user2, self.user3 = create_users()
        self.event = EventList.objects.create(name="Expo", owner=self.user1)
        self.gift = Gift.objects.create(owner=self.user1, created_by=self.user1, title="Gadget", event_list=self.event)

    def _reserve_url(self):
        return reverse("reserve_event_gift", kwargs={"token": self.event.access_token, "gift_id": self.gift.id})

    def _guest_url(self):
        return reverse("event_set_guest", kwargs={"token": self.event.access_token})

    def test_set_guest_name_stores_in_session(self):
        response = self.client.post(
            self._guest_url(), data=json.dumps({"name": "Mario"}), content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data["success"])
        self.assertEqual(self.client.session["guest_name"], "Mario")

    def test_set_guest_name_empty_returns_400(self):
        response = self.client.post(self._guest_url(), data=json.dumps({"name": ""}), content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_set_guest_name_claims_existing_reservations(self):
        old_res = GuestReservation.objects.create(gift=self.gift, reserver_name="Mario", session_key="OLDSESSION")
        response = self.client.post(
            self._guest_url(), data=json.dumps({"name": "mario"}), content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        new_session = self.client.session.session_key
        old_res.refresh_from_db()
        self.assertEqual(old_res.session_key, new_session)

    def test_reserve_gift_as_authenticated_user(self):
        self.client.force_login(self.user2)
        response = self.client.post(self._reserve_url(), data=json.dumps({}), content_type="application/json")
        data = json.loads(response.content)
        self.assertTrue(data["success"])
        self.assertTrue(data["reserved"])
        self.assertTrue(GuestReservation.objects.filter(gift=self.gift, reserver_user=self.user2).exists())

    def test_reserve_gift_as_guest(self):
        session = self.client.session
        session["guest_name"] = "Luigi"
        session.save()
        response = self.client.post(self._reserve_url(), data=json.dumps({}), content_type="application/json")
        data = json.loads(response.content)
        self.assertTrue(data["reserved"])
        self.assertTrue(GuestReservation.objects.filter(gift=self.gift, reserver_name="Luigi").exists())

    def test_unreserve_gift_toggles_off(self):
        self.client.force_login(self.user2)
        GuestReservation.objects.create(gift=self.gift, reserver_user=self.user2, reserver_name="User2")
        response = self.client.post(self._reserve_url(), data=json.dumps({}), content_type="application/json")
        data = json.loads(response.content)
        self.assertFalse(data["reserved"])
        self.assertFalse(GuestReservation.objects.filter(gift=self.gift, reserver_user=self.user2).exists())

    def test_reserve_requires_identity(self):
        response = self.client.post(self._reserve_url(), data=json.dumps({}), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertFalse(data["success"])

    def test_reserve_hidden_gift_returns_404(self):
        self.gift.is_hidden = True
        self.gift.save()
        self.client.force_login(self.user2)
        response = self.client.post(self._reserve_url(), data=json.dumps({}), content_type="application/json")
        self.assertEqual(response.status_code, 404)

    def test_reserve_exclusive_sets_flag(self):
        self.client.force_login(self.user2)
        self.client.post(self._reserve_url(), data=json.dumps({"exclusivity": True}), content_type="application/json")
        res = GuestReservation.objects.get(gift=self.gift, reserver_user=self.user2)
        self.assertTrue(res.exclusivity)

    def test_reserve_non_exclusive_sets_flag_false(self):
        self.client.force_login(self.user2)
        self.client.post(self._reserve_url(), data=json.dumps({"exclusivity": False}), content_type="application/json")
        res = GuestReservation.objects.get(gift=self.gift, reserver_user=self.user2)
        self.assertFalse(res.exclusivity)


class EventTokenAndPhotoTest(TestCase):
    def setUp(self):
        self.user1, self.user2, self.user3 = create_users()
        self.event = EventList.objects.create(name="Concert", owner=self.user1)

    def test_regenerate_token_changes_token(self):
        old_token = self.event.access_token
        self.client.force_login(self.user1)
        response = self.client.post(reverse("regenerate_event_token", kwargs={"token": old_token}))
        self.event.refresh_from_db()
        new_token = self.event.access_token
        self.assertNotEqual(old_token, new_token)
        self.assertRedirects(response, reverse("event_detail", kwargs={"token": new_token}))

    def test_regenerate_token_old_url_returns_404(self):
        old_token = self.event.access_token
        self.client.force_login(self.user1)
        self.client.post(reverse("regenerate_event_token", kwargs={"token": old_token}))
        response = self.client.get(reverse("event_detail", kwargs={"token": old_token}))
        self.assertEqual(response.status_code, 404)

    def test_regenerate_token_forbidden_for_non_owner(self):
        self.client.force_login(self.user2)
        response = self.client.post(reverse("regenerate_event_token", kwargs={"token": self.event.access_token}))
        self.assertEqual(response.status_code, 403)

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_photo_upload_saves_image(self):
        self.client.force_login(self.user1)
        img = make_image()
        response = self.client.post(
            reverse("event_photo_upload", kwargs={"token": self.event.access_token}),
            {"photo": img},
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data["success"])
        self.event.refresh_from_db()
        self.assertTrue(bool(self.event.image))

    def test_photo_upload_forbidden_for_non_owner(self):
        self.client.force_login(self.user2)
        img = make_image()
        response = self.client.post(
            reverse("event_photo_upload", kwargs={"token": self.event.access_token}),
            {"image": img},
        )
        self.assertEqual(response.status_code, 403)


class EventTransferAndLeaveTest(TestCase):
    def setUp(self):
        self.user1, self.user2, self.user3 = create_users()
        self.group = Group.objects.create(name="Family", created_by=self.user1)
        self.group.members.add(self.user1)
        self.event = EventList.objects.create(name="Reunion", owner=self.user1)
        self.gift = Gift.objects.create(owner=self.user1, created_by=self.user1, title="Book", event_list=self.event)
        GuestReservation.objects.create(gift=self.gift, reserver_name="Guest", session_key="sk123")

    def test_transfer_moves_gift_to_personal_list(self):
        self.client.force_login(self.user1)
        self.client.post(
            reverse("transfer_event_gift", kwargs={"token": self.event.access_token, "gift_id": self.gift.id})
        )
        self.gift.refresh_from_db()
        self.assertIsNone(self.gift.event_list)
        self.assertIn(self.group, self.gift.visible_in.all())

    def test_transfer_deletes_guest_reservations(self):
        self.client.force_login(self.user1)
        self.client.post(
            reverse("transfer_event_gift", kwargs={"token": self.event.access_token, "gift_id": self.gift.id})
        )
        self.assertFalse(GuestReservation.objects.filter(gift=self.gift).exists())

    def test_transfer_forbidden_for_non_owner(self):
        self.client.force_login(self.user2)
        response = self.client.post(
            reverse("transfer_event_gift", kwargs={"token": self.event.access_token, "gift_id": self.gift.id})
        )
        self.assertEqual(response.status_code, 403)

    def test_leave_removes_user_from_participants(self):
        self.event.participants.add(self.user2)
        self.client.force_login(self.user2)
        response = self.client.post(reverse("leave_event_list", kwargs={"token": self.event.access_token}))
        self.assertRedirects(response, reverse("dashboard"))
        self.assertNotIn(self.user2, self.event.participants.all())

    def test_leave_requires_login(self):
        response = self.client.post(reverse("leave_event_list", kwargs={"token": self.event.access_token}))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])
        self.assertTrue(Gift.objects.filter(id=self.gift.id).exists())


class PublicDemoTest(TestCase):
    def test_demo_button_is_translated_in_french(self):
        activate("fr")
        try:
            response = self.client.get(reverse("welcome"))
            self.assertContains(response, "Essayer la démo", count=2)
        finally:
            deactivate()

    def test_demo_login_creates_and_logs_in_demo_user(self):
        response = self.client.get(reverse("demo_login"))

        self.assertRedirects(response, reverse("dashboard"))
        demo_user = User.objects.get(email=DEMO_EMAIL)
        self.assertTrue(demo_user.is_demo)
        self.assertTrue(demo_user.is_active)
        self.assertEqual(int(self.client.session["_auth_user_id"]), demo_user.id)
        self.assertTrue(Group.objects.filter(is_demo=True, members=demo_user).exists())
        self.assertTrue(EventList.objects.filter(is_demo=True, owner=demo_user).exists())
        self.assertTrue(Gift.objects.filter(owner__is_demo=True).exists())
        shared_list = SharedList.objects.get(is_demo=True, members=demo_user)
        self.assertEqual(shared_list.members.count(), 3)
        self.assertEqual(shared_list.gifts.count(), 2)
        self.assertTrue(
            SharedGiftPublication.objects.filter(
                gift__shared_list=shared_list,
                published_by=demo_user,
            ).exists()
        )

    def test_lazy_reset_keeps_fresh_demo_data(self):
        call_command("reset_demo")
        demo_user = User.objects.get(email=DEMO_EMAIL)
        Gift.objects.create(owner=demo_user, created_by=demo_user, title="Visitor edit")

        call_command("reset_demo", lazy=True)

        self.assertTrue(Gift.objects.filter(owner=demo_user, title="Visitor edit").exists())

    def test_lazy_reset_rebuilds_stale_demo_data(self):
        call_command("reset_demo")
        demo_user = User.objects.get(email=DEMO_EMAIL)
        demo_user.date_joined = timezone.now() - timedelta(minutes=16)
        demo_user.save(update_fields=["date_joined"])
        Gift.objects.create(owner=demo_user, created_by=demo_user, title="Visitor edit")

        call_command("reset_demo", lazy=True)

        self.assertFalse(Gift.objects.filter(title="Visitor edit").exists())
        self.assertTrue(User.objects.filter(email=DEMO_EMAIL, is_demo=True).exists())

    def test_real_user_cannot_join_demo_group(self):
        call_command("reset_demo")
        demo_group = Group.objects.filter(is_demo=True).first()
        real_user = User.objects.create_user(
            username="real@test.com",
            email="real@test.com",
            password="password",
            is_verified=True,
            nickname="Real",
            onboarding_version=CURRENT_ONBOARDING_VERSION,
            profile_completed_at=timezone.now(),
        )

        self.client.force_login(real_user)
        response = self.client.get(reverse("join_group", kwargs={"token": demo_group.group_token}))

        self.assertEqual(response.status_code, 403)
        self.assertNotIn(real_user, demo_group.members.all())

    def test_demo_profile_cannot_be_modified(self):
        call_command("reset_demo")
        demo_user = User.objects.get(email=DEMO_EMAIL)
        self.client.force_login(demo_user)

        response = self.client.post(
            reverse("account"),
            {
                "nickname": "Changed",
                "email": "changed@example.com",
            },
        )

        self.assertEqual(response.status_code, 403)
        demo_user.refresh_from_db()
        self.assertEqual(demo_user.email, DEMO_EMAIL)
