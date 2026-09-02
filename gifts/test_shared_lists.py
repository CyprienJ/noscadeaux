import json
from datetime import timedelta

from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Gift, Group, Reservation, SharedGiftPublication, SharedList, SharedListMembership, User
from .onboarding import CURRENT_ONBOARDING_VERSION


class SharedListTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(
            email="alice@example.com",
            username="alice@example.com",
            password="password",
            nickname="Alice",
            is_verified=True,
            onboarding_version=CURRENT_ONBOARDING_VERSION,
            profile_completed_at=timezone.now(),
        )
        self.bob = User.objects.create_user(
            email="bob@example.com",
            username="bob@example.com",
            password="password",
            nickname="Bob",
            is_verified=True,
            onboarding_version=CURRENT_ONBOARDING_VERSION,
            profile_completed_at=timezone.now(),
        )
        self.carol = User.objects.create_user(
            email="carol@example.com",
            username="carol@example.com",
            password="password",
            nickname="Carol",
            is_verified=True,
            onboarding_version=CURRENT_ONBOARDING_VERSION,
            profile_completed_at=timezone.now(),
        )
        self.family = Group.objects.create(name="Alice family", created_by=self.alice)
        self.family.members.add(self.alice, self.carol)
        self.common_group = Group.objects.create(name="Common friends", created_by=self.alice)
        self.common_group.members.add(self.alice, self.bob, self.carol)
        self.shared_list = SharedList.objects.create(name="Our home")
        SharedListMembership.objects.create(shared_list=self.shared_list, user=self.alice)
        SharedListMembership.objects.create(shared_list=self.shared_list, user=self.bob)

    def test_any_member_can_add_an_eligible_member(self):
        self.client.force_login(self.bob)
        response = self.client.post(
            reverse("add_shared_list_member", args=[self.shared_list.id]),
            {"user_id": self.carol.id},
        )
        self.assertRedirects(response, reverse("shared_list_detail", args=[self.shared_list.id]))
        self.assertTrue(self.shared_list.members.filter(id=self.carol.id).exists())

    def test_adding_a_member_cancels_their_existing_reservations_on_the_list(self):
        gift = Gift.objects.create(
            owner=self.alice, shared_list=self.shared_list, created_by=self.bob, title="Coffee machine"
        )
        Reservation.objects.create(gift=gift, reserver=self.carol, exclusivity=True)
        gift.group_reserved_on = self.common_group
        gift.save(update_fields=["group_reserved_on"])

        self.client.force_login(self.bob)
        self.client.post(
            reverse("add_shared_list_member", args=[self.shared_list.id]),
            {"user_id": self.carol.id},
        )

        gift.refresh_from_db()
        self.assertFalse(gift.reservation.exists())
        self.assertIsNone(gift.group_reserved_on)

    def test_member_can_publish_in_a_group_the_other_member_cannot_access(self):
        self.client.force_login(self.alice)
        self.client.post(
            reverse("add_shared_gift", args=[self.shared_list.id]),
            {"title": "Coffee machine", "visible_in": [self.family.id]},
        )
        gift = self.shared_list.gifts.get()

        self.assertTrue(
            SharedGiftPublication.objects.filter(gift=gift, group=self.family, published_by=self.alice).exists()
        )
        self.client.force_login(self.bob)
        response = self.client.get(reverse("shared_list_detail", args=[self.shared_list.id]))
        self.assertContains(response, self.family.name)
        response = self.client.get(
            reverse("shared_list_detail", args=[self.shared_list.id]),
            {"from_group": self.family.id, "published_by": self.alice.id},
        )
        self.assertEqual(response.status_code, 404)

    def test_managers_see_group_exclusive_wishes_but_never_reservations(self):
        gift = Gift.objects.create(
            owner=self.alice, shared_list=self.shared_list, created_by=self.bob, title="Coffee machine"
        )
        SharedGiftPublication.objects.create(gift=gift, group=self.family, published_by=self.alice)
        Reservation.objects.create(gift=gift, reserver=self.carol, exclusivity=True)

        self.client.force_login(self.bob)
        response = self.client.get(reverse("shared_list_detail", args=[self.shared_list.id]))
        self.assertContains(response, gift.title)
        self.assertContains(response, self.family.name)
        self.assertNotContains(response, self.carol.email)
        self.assertNotContains(response, "Reserved by")

        response = self.client.post(
            reverse("reserve_gift", args=[gift.id]),
            data=json.dumps({"exclusivity": True, "user_id": self.bob.id, "group_id": self.family.id}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_group_member_can_view_and_reserve_a_published_shared_wish(self):
        gift = Gift.objects.create(
            owner=self.alice, shared_list=self.shared_list, created_by=self.bob, title="Coffee machine"
        )
        SharedGiftPublication.objects.create(gift=gift, group=self.family, published_by=self.alice)
        self.client.force_login(self.carol)
        response = self.client.get(
            reverse("shared_list_detail", args=[self.shared_list.id]),
            {"from_group": self.family.id, "published_by": self.alice.id},
        )
        self.assertContains(response, gift.title)
        response = self.client.post(
            reverse("reserve_gift", args=[gift.id]),
            data=json.dumps({"exclusivity": True, "user_id": self.carol.id, "group_id": self.family.id}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Reservation.objects.filter(gift=gift, reserver=self.carol).exists())

    def test_moving_reserved_personal_wish_cancels_and_notifies(self):
        gift = Gift.objects.create(owner=self.alice, created_by=self.alice, title="Coffee machine")
        gift.visible_in.add(self.family)
        Reservation.objects.create(gift=gift, reserver=self.carol, exclusivity=True)

        self.client.force_login(self.alice)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("move_gift_to_shared_list", args=[gift.id, self.shared_list.id]))

        self.assertRedirects(response, reverse("shared_list_detail", args=[self.shared_list.id]))
        gift.refresh_from_db()
        self.assertEqual(gift.shared_list, self.shared_list)
        self.assertFalse(gift.reservation.exists())
        self.assertFalse(gift.visible_in.exists())
        self.assertTrue(
            SharedGiftPublication.objects.filter(gift=gift, group=self.family, published_by=self.alice).exists()
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.carol.email])
        self.assertIn(gift.title, mail.outbox[0].body)

    def test_soft_delete_emails_members_and_can_be_restored(self):
        self.client.force_login(self.alice)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("delete_shared_list", args=[self.shared_list.id]))
        self.assertRedirects(response, reverse("dashboard"))
        self.shared_list.refresh_from_db()
        self.assertIsNotNone(self.shared_list.deleted_at)
        self.assertEqual(len(mail.outbox), 2)

        restore_url = reverse("restore_shared_list", args=[self.shared_list.id, self.shared_list.restore_token])
        response = self.client.post(restore_url)
        self.assertRedirects(response, reverse("shared_list_detail", args=[self.shared_list.id]))
        self.shared_list.refresh_from_db()
        self.assertIsNone(self.shared_list.deleted_at)

    def test_purge_removes_lists_after_48_hours(self):
        self.shared_list.deleted_at = timezone.now() - timedelta(hours=49)
        self.shared_list.save(update_fields=["deleted_at"])
        call_command("purge_deleted_shared_lists")
        self.assertFalse(SharedList.objects.filter(id=self.shared_list.id).exists())

    def test_shared_wish_is_integrated_only_into_the_publisher_group_list(self):
        gift = Gift.objects.create(
            owner=self.alice, shared_list=self.shared_list, created_by=self.alice, title="Coffee machine"
        )
        SharedGiftPublication.objects.create(gift=gift, group=self.common_group, published_by=self.alice)

        self.client.force_login(self.carol)
        alice_view = self.client.get(
            reverse("view_list", args=[self.alice.id]),
            {"from_group": self.common_group.id},
        )
        bob_view = self.client.get(
            reverse("view_list", args=[self.bob.id]),
            {"from_group": self.common_group.id},
        )

        self.assertContains(alice_view, gift.title)
        self.assertNotContains(bob_view, gift.title)

    def test_same_shared_wish_can_be_integrated_into_both_member_lists(self):
        gift = Gift.objects.create(
            owner=self.alice, shared_list=self.shared_list, created_by=self.alice, title="Coffee machine"
        )
        SharedGiftPublication.objects.create(gift=gift, group=self.common_group, published_by=self.alice)
        SharedGiftPublication.objects.create(gift=gift, group=self.common_group, published_by=self.bob)

        self.client.force_login(self.carol)
        alice_view = self.client.get(
            reverse("view_list", args=[self.alice.id]),
            {"from_group": self.common_group.id},
        )
        bob_view = self.client.get(
            reverse("view_list", args=[self.bob.id]),
            {"from_group": self.common_group.id},
        )

        self.assertContains(alice_view, gift.title)
        self.assertContains(bob_view, gift.title)

    def test_each_member_list_contains_only_shared_wishes_published_by_that_member(self):
        alice_gift = Gift.objects.create(
            owner=self.alice, shared_list=self.shared_list, created_by=self.alice, title="Alice wish"
        )
        bob_gift = Gift.objects.create(
            owner=self.bob, shared_list=self.shared_list, created_by=self.bob, title="Bob wish"
        )
        SharedGiftPublication.objects.create(gift=alice_gift, group=self.common_group, published_by=self.alice)
        SharedGiftPublication.objects.create(gift=bob_gift, group=self.common_group, published_by=self.bob)

        self.client.force_login(self.carol)
        alice_view = self.client.get(
            reverse("view_list", args=[self.alice.id]),
            {"from_group": self.common_group.id},
        )
        bob_view = self.client.get(
            reverse("view_list", args=[self.bob.id]),
            {"from_group": self.common_group.id},
        )

        self.assertContains(alice_view, alice_gift.title)
        self.assertNotContains(alice_view, bob_gift.title)
        self.assertContains(bob_view, bob_gift.title)
        self.assertNotContains(bob_view, alice_gift.title)

    def test_shared_wish_is_absent_from_a_member_who_did_not_publish_it(self):
        gift = Gift.objects.create(
            owner=self.alice, shared_list=self.shared_list, created_by=self.alice, title="Coffee machine"
        )
        SharedGiftPublication.objects.create(gift=gift, group=self.common_group, published_by=self.alice)

        self.client.force_login(self.carol)
        response = self.client.get(
            reverse("view_list", args=[self.bob.id]),
            {"from_group": self.common_group.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, gift.title)

    def test_group_list_orders_personal_then_shared_then_surprises(self):
        personal = Gift.objects.create(owner=self.alice, created_by=self.alice, title="Personal wish")
        personal.visible_in.add(self.common_group)
        shared = Gift.objects.create(
            owner=self.alice, shared_list=self.shared_list, created_by=self.bob, title="Shared wish"
        )
        SharedGiftPublication.objects.create(gift=shared, group=self.common_group, published_by=self.alice)
        surprise = Gift.objects.create(owner=self.alice, created_by=self.carol, title="Surprise wish")
        surprise.visible_in.add(self.common_group)

        self.client.force_login(self.carol)
        response = self.client.get(
            reverse("view_list", args=[self.alice.id]),
            {"from_group": self.common_group.id},
        )
        content = response.content.decode()

        self.assertLess(content.index(personal.title), content.index(shared.title))
        self.assertLess(content.index(shared.title), content.index(surprise.title))

    def test_shared_list_member_sees_integrated_wish_without_reservation_state(self):
        gift = Gift.objects.create(
            owner=self.alice, shared_list=self.shared_list, created_by=self.alice, title="Shared wish"
        )
        SharedGiftPublication.objects.create(gift=gift, group=self.common_group, published_by=self.alice)
        Reservation.objects.create(gift=gift, reserver=self.carol, exclusivity=True)

        self.client.force_login(self.bob)
        response = self.client.get(
            reverse("view_list", args=[self.alice.id]),
            {"from_group": self.common_group.id},
        )

        self.assertContains(response, gift.title)
        self.assertNotContains(response, f"reserveModal{gift.id}")
        self.assertNotContains(response, self.carol.email)

    def test_member_edit_replaces_only_their_own_group_visibility(self):
        gift = Gift.objects.create(
            owner=self.alice, shared_list=self.shared_list, created_by=self.alice, title="Coffee machine"
        )
        SharedGiftPublication.objects.create(gift=gift, group=self.family, published_by=self.alice)

        self.client.force_login(self.bob)
        response = self.client.post(
            reverse("edit_shared_gift", args=[self.shared_list.id, gift.id]),
            {"title": gift.title, "visible_in": [self.common_group.id]},
        )

        self.assertRedirects(response, reverse("shared_list_detail", args=[self.shared_list.id]))
        self.assertTrue(
            SharedGiftPublication.objects.filter(gift=gift, group=self.family, published_by=self.alice).exists()
        )
        self.assertTrue(
            SharedGiftPublication.objects.filter(gift=gift, group=self.common_group, published_by=self.bob).exists()
        )

    def test_removing_from_two_member_list_transfers_wishes(self):
        gift = Gift.objects.create(
            owner=self.alice, shared_list=self.shared_list, created_by=self.bob, title="Coffee machine"
        )
        self.client.force_login(self.alice)
        response = self.client.post(
            reverse("remove_shared_list_member", args=[self.shared_list.id, self.bob.id]),
            {"resolution": "transfer"},
        )
        self.assertRedirects(response, reverse("dashboard"))
        gift.refresh_from_db()
        self.assertEqual(gift.owner, self.alice)
        self.assertIsNone(gift.shared_list)
        self.assertFalse(SharedList.objects.filter(id=self.shared_list.id).exists())
