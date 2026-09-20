import csv
import io
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils.translation import gettext as _

from .models import EventList, Gift, GiftTag, Group, SecretSantaAssignment, SharedList
from .tests import create_users


class GiftListCsvExportTest(TestCase):
    def setUp(self):
        self.owner, self.viewer, self.other = create_users()
        self.group = Group.objects.create(name="Family")
        self.group.members.add(self.owner, self.viewer)
        self.url = reverse("export_gift_list", args=[self.owner.id])
        self.wish = self._gift('Book, "édition spéciale"', description="First line\nSecond line", price=Decimal("0"))
        self.draft = self._gift("Draft", is_draft=True)
        self.surprise = self._gift("Surprise", created_by=self.viewer)
        self.client.force_login(self.owner)

    def _gift(self, title, **kwargs):
        kwargs.setdefault("created_by", self.owner)
        return Gift.objects.create(owner=self.owner, title=title, **kwargs)

    def _export_rows(self, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertEqual(response["Content-Disposition"], 'attachment; filename="list-User1.csv"')
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))
        return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))

    def test_owner_export_preserves_csv_fields_and_hides_surprises(self):
        tag = GiftTag.objects.get(slug=GiftTag.Slug.BOOKS)
        self.wish.tags.add(tag)
        rows = self._export_rows()

        self.assertEqual(len(rows[0]), 8)
        self.assertEqual([row[0] for row in rows[1:]], [self.wish.title, self.draft.title])
        self.assertEqual(
            rows[1],
            [
                self.wish.title,
                self.wish.description,
                "",
                "0.00",
                "EUR",
                tag.label,
                _("Wish"),
                self.wish.created_at.strftime("%Y-%m-%d"),
            ],
        )
        self.assertEqual(rows[2][3], "")
        self.assertEqual(rows[2][6], _("Draft"))

    def test_export_excludes_offered_event_and_shared_gifts(self):
        event = EventList.objects.create(name="Birthday", owner=self.owner)
        shared_list = SharedList.objects.create(name="Shared wishes")
        for fields in ({"offered": True}, {"event_list": event}, {"shared_list": shared_list}):
            self._gift("Excluded", **fields)

        self.assertEqual([row[0] for row in self._export_rows()[1:]], [self.wish.title, self.draft.title])

    def test_viewer_export_filters_drafts_and_group_visibility(self):
        other_group = Group.objects.create(name="Other group")
        self._gift("Hidden wish").visible_in.add(other_group)
        self.wish.visible_in.add(self.group, other_group)
        self.client.force_login(self.viewer)

        rows = self._export_rows(from_group=self.group.id)[1:]

        self.assertEqual([row[0] for row in rows], [self.wish.title, self.surprise.title])
        self.assertEqual([row[6] for row in rows], [_("Wish"), _("Surprise")])

    def test_managed_recipient_gifts_are_wishes(self):
        self.owner.is_managed = True
        self.owner.save(update_fields=["is_managed"])
        self.client.force_login(self.viewer)

        self.assertEqual([row[6] for row in self._export_rows()[1:]], [_("Wish"), _("Wish")])

    def test_export_requires_login_and_get(self):
        for method in ("post", "put", "patch", "delete", "head", "options"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(self.url)
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response["Allow"], "GET")
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_list_and_export_share_access_rules(self):
        self.client.force_login(self.other)
        event = EventList.objects.create(name="Christmas", owner=self.other)
        for endpoint in ("view_list", "export_gift_list"):
            url = reverse(endpoint, args=[self.owner.id])
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.get(url).status_code, 403)
                assignment = SecretSantaAssignment.objects.create(event=event, giver=self.other, receiver=self.owner)
                self.assertEqual(self.client.get(url).status_code, 200)
                self.assertEqual(self.client.get(url, {"from_group": self.group.id}).status_code, 403)
                assignment.delete()

    def test_export_rejects_non_common_group_and_missing_resources(self):
        other_group = Group.objects.create(name="Private group")
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(self.url, {"from_group": other_group.id}).status_code, 403)
        self.assertEqual(self.client.get(self.url, {"from_group": 999999}).status_code, 404)
        self.assertEqual(self.client.get(reverse("export_gift_list", args=[999999])).status_code, 404)

    def test_demo_scope_is_enforced(self):
        self.owner.is_demo = True
        self.owner.save(update_fields=["is_demo"])
        self.client.force_login(self.viewer)

        self.assertEqual(self.client.get(self.url).status_code, 403)
