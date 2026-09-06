from unittest.mock import patch

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from .models import Gift, Group, ManagedMember, User


class ManagedMemberManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="member", email="member@example.com", nickname="Member", is_verified=True
        )
        cls.outsider = User.objects.create_user(
            username="outsider", email="outsider@example.com", nickname="Outsider", is_verified=True
        )
        cls.group = Group.objects.create(name="Family", created_by=cls.user)
        cls.group.members.add(cls.user)

    def setUp(self):
        self.client.force_login(self.user)

    def add_member(self):
        response = self.client.post(reverse("add_managed_member", args=[self.group.pk]), {"name": "Camille"})
        self.assertEqual(response.status_code, 302)
        return self.group.managed_members.get()

    def test_creation_exposes_management_on_group_and_list_pages(self):
        member = self.add_member()
        self.assertEqual(member.user.nickname, "Camille")
        self.assertTrue(member.user.is_managed)
        self.assertFalse(member.user.is_active)
        self.assertTrue(self.group.members.filter(pk=member.user_id).exists())
        self.assertTrue(member.color)

        for url in (
            reverse("group_detail", args=[self.group.pk]),
            f"{reverse('view_list', args=[member.user_id])}?from_group={self.group.pk}",
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, 'class="nc-member-manage-btn"')
                self.assertContains(response, reverse("rename_managed_member", args=[self.group.pk, member.pk]))
                self.assertContains(response, reverse("delete_managed_member", args=[self.group.pk, member.pk]))
                self.assertContains(response, "function openManageManagedModal(")

    def test_creation_rolls_back_when_profile_cannot_be_created(self):
        with (
            patch("gifts.groups.ManagedMember.objects.create", side_effect=RuntimeError("Profile creation failed")),
            self.assertRaises(RuntimeError),
        ):
            self.client.post(reverse("add_managed_member", args=[self.group.pk]), {"name": "Camille"})
        self.assertFalse(User.objects.filter(is_managed=True).exists())

    def test_member_can_be_renamed(self):
        member = self.add_member()
        response = self.client.post(
            reverse("rename_managed_member", args=[self.group.pk, member.pk]), {"name": "Charlie"}
        )
        self.assertEqual(response.status_code, 302)
        member.refresh_from_db()
        self.assertEqual(member.name, "Charlie")
        self.assertEqual(member.user.nickname, "Charlie")

    def test_member_and_wishes_can_be_deleted(self):
        member = self.add_member()
        gift = Gift.objects.create(title="Book", owner=member.user, created_by=self.user)
        response = self.client.post(reverse("delete_managed_member", args=[self.group.pk, member.pk]))
        self.assertRedirects(response, reverse("group_detail", args=[self.group.pk]))
        self.assertFalse(User.objects.filter(pk=member.user_id).exists())
        self.assertFalse(ManagedMember.objects.filter(pk=member.pk).exists())
        self.assertFalse(Gift.objects.filter(pk=gift.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    def test_outsider_cannot_manage_member(self):
        member = self.add_member()
        self.client.force_login(self.outsider)
        for action in ("rename_managed_member", "delete_managed_member"):
            with self.subTest(action=action):
                response = self.client.post(reverse(action, args=[self.group.pk, member.pk]), {"name": "Changed"})
                self.assertEqual(response.status_code, 403)
        member.refresh_from_db()
        self.assertEqual(member.user.nickname, "Camille")

    def test_management_rejects_another_group(self):
        member = self.add_member()
        other_group = Group.objects.create(name="Other family")
        other_group.members.add(self.user)
        for action in ("rename_managed_member", "delete_managed_member"):
            with self.subTest(action=action):
                response = self.client.post(reverse(action, args=[other_group.pk, member.pk]), {"name": "Changed"})
                self.assertEqual(response.status_code, 404)
        member.refresh_from_db()
        self.assertEqual(member.user.nickname, "Camille")

    def test_another_group_member_can_edit_and_delete_a_managed_members_gift(self):
        member = self.add_member()
        gift = Gift.objects.create(title="Book", owner=member.user, created_by=self.user)
        self.group.members.add(self.outsider)
        self.client.force_login(self.outsider)

        response = self.client.post(reverse("edit_gift", args=[gift.pk]), {"title": "Illustrated book"})
        self.assertEqual(response.status_code, 302)
        gift.refresh_from_db()
        self.assertEqual(gift.title, "Illustrated book")

        response = self.client.post(reverse("delete_gift", args=[gift.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Gift.objects.filter(pk=gift.pk).exists())
        self.assertTrue(User.objects.filter(pk=member.user_id).exists())

    def test_outsider_cannot_edit_or_delete_a_managed_members_gift(self):
        member = self.add_member()
        gift = Gift.objects.create(title="Book", owner=member.user, created_by=self.user)
        self.client.force_login(self.outsider)
        for action in ("edit_gift", "delete_gift"):
            with self.subTest(action=action):
                response = self.client.post(reverse(action, args=[gift.pk]), {"title": "Changed"})
                self.assertEqual(response.status_code, 403)
        gift.refresh_from_db()
        self.assertEqual(gift.title, "Book")


class ManagedMemberProfileMigrationTests(TransactionTestCase):
    migrate_from = [("gifts", "0038_shared_gift_publications")]
    migrate_to = [("gifts", "0039_backfill_managed_member_profiles")]

    def test_existing_accounts_are_repaired_without_changing_existing_profiles(self):
        executor = MigrationExecutor(connection)
        self.addCleanup(executor.migrate, executor.loader.graph.leaf_nodes())
        executor.migrate(self.migrate_from)
        apps = executor.loader.project_state(self.migrate_from).apps
        users = apps.get_model("gifts", "User")
        groups = apps.get_model("gifts", "Group")
        profiles = apps.get_model("gifts", "ManagedMember")
        gifts = apps.get_model("gifts", "Gift")

        group = groups.objects.create(name="Family", group_token="FAMILY")
        other_group = groups.objects.create(name="Other family", group_token="OTHER")
        managed = users.objects.create(
            email="managed@example.com", username="managed", nickname="Camille", is_managed=True
        )
        existing = users.objects.create(
            email="existing@example.com", username="existing", nickname="Alex", is_managed=True
        )
        regular = users.objects.create(
            email="regular@example.com", username="regular", nickname="Regular", is_verified=True
        )
        orphan = users.objects.create(email="orphan@example.com", username="orphan", is_managed=True)
        group.members.add(managed, existing, regular)
        other_group.members.add(managed)
        profile = profiles.objects.create(user=existing, group=group, name="Alex", color="#123456")
        gift = gifts.objects.create(title="Book", owner=managed, created_by=regular)

        self.client.force_login(User.objects.get(pk=regular.pk))
        # Reproduce the reported refusal for an existing account missing its profile.
        for action in ("edit_gift", "delete_gift"):
            with self.subTest(before_repair=action):
                response = self.client.post(reverse(action, args=[gift.pk]), {"title": "Illustrated book"})
                self.assertEqual(response.status_code, 403)

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)

        repaired = ManagedMember.objects.get(user_id=managed.pk)
        self.assertEqual(repaired.name, "Camille")
        self.assertEqual(repaired.group_id, group.pk)
        self.assertTrue(repaired.color)
        self.assertEqual(ManagedMember.objects.get(pk=profile.pk).color, "#123456")
        self.assertEqual(ManagedMember.objects.count(), 2)
        self.assertFalse(ManagedMember.objects.filter(user_id__in=[regular.pk, orphan.pk]).exists())
        self.assertEqual(Gift.objects.get(pk=gift.pk).owner_id, managed.pk)

        # Reapplying the repair must neither duplicate profiles nor remove wishes.
        executor.migrate(self.migrate_from)
        MigrationExecutor(connection).migrate(self.migrate_to)
        self.assertEqual(ManagedMember.objects.count(), 2)
        self.assertTrue(Gift.objects.filter(pk=gift.pk).exists())

        response = self.client.post(reverse("edit_gift", args=[gift.pk]), {"title": "Illustrated book"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Gift.objects.get(pk=gift.pk).title, "Illustrated book")
        response = self.client.post(reverse("delete_gift", args=[gift.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Gift.objects.filter(pk=gift.pk).exists())
