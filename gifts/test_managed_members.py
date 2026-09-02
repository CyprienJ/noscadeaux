from io import StringIO
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse

from gifts.managed_members import MANAGED_MEMBER_COLORS, create_managed_member
from gifts.models import Gift, Group, ManagedMember, User
from gifts.tests import create_users


class ManagedPeopleTest(TestCase):
    def setUp(self):
        self.owner, self.member, self.outsider = create_users()
        self.group = Group.objects.create(name="Family", created_by=self.owner)
        self.group.members.add(self.owner, self.member)
        self.client.force_login(self.owner)

    def test_add_two_people_from_invitations_and_keep_their_lists_accessible(self):
        for name in ["Alice", "Bob"]:
            response = self.client.post(
                reverse("add_managed_member", args=[self.group.pk]),
                {"name": name, "return_to": "invitations"},
                follow=True,
            )
            self.assertRedirects(response, reverse("group_invitations", args=[self.group.pk]))
            self.assertContains(response, name)
        self.assertEqual(self.group.managed_members.count(), 2)
        person = self.group.managed_members.first()
        self.assertFalse(person.user.is_active)
        self.assertFalse(person.user.has_usable_password())
        self.assertIn(person.color, MANAGED_MEMBER_COLORS)
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse("view_list", args=[person.user_id])).status_code, 200)

    def test_creation_rolls_back_when_profile_or_membership_fails(self):
        original_count = User.objects.count()
        with (
            patch("gifts.managed_members.ManagedMember.objects.create", side_effect=IntegrityError),
            self.assertRaises(IntegrityError),
        ):
            create_managed_member(self.group, self.owner, "Child")
        self.assertEqual(User.objects.count(), original_count)
        self.assertFalse(ManagedMember.objects.exists())
        with (
            patch.object(type(self.group.members), "add", side_effect=IntegrityError),
            self.assertRaises(IntegrityError),
        ):
            create_managed_member(self.group, self.owner, "Child")
        self.assertEqual(User.objects.count(), original_count)
        self.assertFalse(ManagedMember.objects.exists())

    def test_invalid_names_and_outsiders_create_nothing(self):
        for name in ["   ", "x" * 101]:
            self.client.post(reverse("add_managed_member", args=[self.group.pk]), {"name": name})
        with self.assertRaises(PermissionDenied):
            create_managed_member(self.group, self.outsider, "Child")
        self.assertFalse(ManagedMember.objects.exists())

    def test_member_can_rename_edit_and_delete_person_and_gifts(self):
        person = create_managed_member(self.group, self.owner, "Child")
        gift = Gift.objects.create(owner=person.user, created_by=self.owner, title="Bike")
        self.client.force_login(self.member)
        self.client.post(reverse("rename_managed_member", args=[self.group.pk, person.pk]), {"name": "New name"})
        person.refresh_from_db()
        self.assertEqual(person.name, "New name")
        self.assertEqual(person.user.nickname, "New name")
        self.client.post(reverse("edit_gift", args=[gift.pk]), {"title": "New bike"})
        gift.refresh_from_db()
        self.assertEqual(gift.title, "New bike")
        self.client.post(reverse("delete_managed_member", args=[self.group.pk, person.pk]))
        self.assertFalse(User.objects.filter(pk=person.user_id).exists())
        self.assertFalse(Gift.objects.filter(pk=gift.pk).exists())
        self.assertFalse(ManagedMember.objects.filter(pk=person.pk).exists())

    def test_outsider_cannot_mutate_person(self):
        person = create_managed_member(self.group, self.owner, "Child")
        self.client.force_login(self.outsider)
        for route in ["rename_managed_member", "delete_managed_member"]:
            self.assertEqual(
                self.client.post(reverse(route, args=[self.group.pk, person.pk]), {"name": "Changed"}).status_code, 403
            )
        person.refresh_from_db()
        self.assertEqual(person.name, "Child")

    def test_group_deletion_removes_managed_identities(self):
        person = create_managed_member(self.group, self.owner, "Child")
        Gift.objects.create(owner=person.user, created_by=person.user, title="Bike")
        self.group.delete()
        self.assertFalse(User.objects.filter(pk=person.user_id).exists())
        self.assertFalse(ManagedMember.objects.exists())
        self.assertFalse(Gift.objects.exists())
        self.assertTrue(User.objects.filter(pk=self.owner.pk).exists())

    def test_direct_user_deletion_has_no_recursive_cascade(self):
        person = create_managed_member(self.group, self.owner, "Child")
        person.user.delete()
        self.assertFalse(ManagedMember.objects.exists())

    def test_group_survives_its_creators_deletion_while_members_remain(self):
        person = create_managed_member(self.group, self.owner, "Child")
        self.owner.delete()
        self.assertTrue(Group.objects.filter(pk=self.group.pk).exists())
        self.assertTrue(User.objects.filter(pk=person.user_id).exists())
        self.assertTrue(User.objects.filter(pk=self.member.pk).exists())

    def test_last_members_deletion_removes_the_group_and_its_managed_people(self):
        self.group.members.remove(self.member)
        person = create_managed_member(self.group, self.owner, "Child")
        self.owner.delete()
        self.assertFalse(Group.objects.filter(pk=self.group.pk).exists())
        self.assertFalse(User.objects.filter(pk=person.user_id).exists())
        self.assertFalse(ManagedMember.objects.exists())

    def test_bulk_user_deletion_cleans_up_managed_people_without_recursion(self):
        person = create_managed_member(self.group, self.owner, "Child")
        User.objects.filter(pk__in=[self.owner.pk, person.user_id]).delete()
        self.assertFalse(ManagedMember.objects.exists())
        self.assertFalse(User.objects.filter(pk=person.user_id).exists())
        self.assertTrue(Group.objects.filter(pk=self.group.pk).exists())

    def test_bulk_deletion_of_all_real_members_removes_managed_identities(self):
        person = create_managed_member(self.group, self.owner, "Child")
        User.objects.filter(pk__in=[self.owner.pk, self.member.pk]).delete()
        self.assertFalse(User.objects.filter(pk=person.user_id).exists())
        self.assertFalse(Group.objects.filter(pk=self.group.pk).exists())

    def test_managed_member_color_follows_rank_within_the_group(self):
        other_group = Group.objects.create(name="Friends", created_by=self.owner)
        other_group.members.add(self.owner)
        # A person in another group must not shift this group's colour sequence.
        create_managed_member(other_group, self.owner, "Elsewhere")

        colors = [create_managed_member(self.group, self.owner, name).color for name in ("A", "B", "C")]

        self.assertEqual(colors, list(MANAGED_MEMBER_COLORS[:3]))

    def test_audit_managed_members_lists_ambiguous_identities(self):
        healthy = create_managed_member(self.group, self.owner, "Child")
        second_group = Group.objects.create(name="Friends", created_by=self.owner)
        in_two_groups = User.objects.create_user(
            email="ghost1@noscadeaux.internal",
            username="ghost1@noscadeaux.internal",
            password="!",
            nickname="Ghost",
            is_managed=True,
            is_active=False,
        )
        in_two_groups.gift_groups.add(self.group, second_group)
        active_managed = User.objects.create_user(
            email="ghost2@noscadeaux.internal",
            username="ghost2@noscadeaux.internal",
            password="!",
            nickname="Active ghost",
            is_managed=True,
            is_active=True,
        )
        active_managed.gift_groups.add(self.group)
        without_group = User.objects.create_user(
            email="ungrouped@noscadeaux.internal",
            username="ungrouped@noscadeaux.internal",
            is_managed=True,
            is_active=False,
        )
        repairable = User.objects.create_user(
            email="repairable@noscadeaux.internal",
            username="repairable@noscadeaux.internal",
            is_managed=True,
            is_active=False,
        )
        repairable.gift_groups.add(self.group)

        out = StringIO()
        with self.assertNumQueries(1):
            call_command("audit_managed_members", stdout=out)
        output = out.getvalue()

        self.assertIn(f"pk={in_two_groups.pk} is_active=False groups=2", output)
        self.assertIn(f"pk={active_managed.pk} is_active=True groups=1", output)
        self.assertIn(f"pk={without_group.pk} is_active=False groups=0", output)
        self.assertNotIn(f"pk={healthy.user_id} ", output)
        self.assertNotIn(f"pk={repairable.pk} ", output)

    def test_audit_managed_members_is_quiet_when_clean(self):
        create_managed_member(self.group, self.owner, "Child")
        out = StringIO()
        call_command("audit_managed_members", stdout=out)
        self.assertIn("No ambiguous managed identities", out.getvalue())

    def test_mutations_require_post_and_csrf(self):
        url = reverse("add_managed_member", args=[self.group.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        self.assertEqual(client.post(url, {"name": "Child"}).status_code, 403)

    def test_leaving_a_group_that_still_has_a_real_member_keeps_it(self):
        person = create_managed_member(self.group, self.owner, "Child")
        self.client.force_login(self.owner)

        response = self.client.post(reverse("leave_group", args=[self.group.pk]))

        self.assertRedirects(response, reverse("dashboard"))
        self.group.refresh_from_db()
        self.assertFalse(self.group.members.filter(pk=self.owner.pk).exists())
        self.assertTrue(self.group.members.filter(pk=self.member.pk).exists())
        self.assertTrue(User.objects.filter(pk=person.user_id).exists())

    def test_last_real_member_leaving_deletes_the_group_and_its_managed_people(self):
        self.group.members.remove(self.member)
        person = create_managed_member(self.group, self.owner, "Child")
        Gift.objects.create(owner=person.user, created_by=self.owner, title="Bike")
        self.client.force_login(self.owner)

        response = self.client.post(reverse("leave_group", args=[self.group.pk]))

        self.assertRedirects(response, reverse("dashboard"))
        self.assertFalse(Group.objects.filter(pk=self.group.pk).exists())
        self.assertFalse(User.objects.filter(pk=person.user_id).exists())
        self.assertFalse(ManagedMember.objects.exists())
        self.assertFalse(Gift.objects.exists())
        self.assertTrue(User.objects.filter(pk=self.owner.pk).exists())
