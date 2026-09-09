# Shared lists

[Lists and wishes](lists.md)

A shared list has several managing members. Its gifts belong functionally to the
shared list, even though `Gift.owner` remains a required compatibility field.
Do not use that one foreign key as the authorization policy for shared wishes.

## Membership and publication

`SharedListMembership` connects users to the list. Current members can manage
the list and its wishes. A person invited to manage it must be an active,
non-managed user in the same demo scope and share a group with the actor.

Each member publishes wishes into their own groups independently.
`SharedGiftPublication` records the gift, group, and publishing member; that
triple is unique. Editing one's publication settings replaces only that person's
publications, leaving other members' choices intact.

Managers can open the list directly. A visitor needs a group context containing a
valid publication by a current member. The optional `published_by` parameter
selects whose publications to show. Deleted lists are excluded from normal views.

## Reservation privacy

All shared-list members are treated as recipients. Their views hide reservation
state and comments, and reservation validation excludes them as buyers. Adding
someone to the shared list deletes that person's existing reservations on its
gifts. If no reservation remains on a gift, its reservation group is cleared.

## Moving a personal wish into a shared list

The actor must own an active, non-event personal gift and manage the destination.
The move runs in a transaction and:

1. Captures the current visibility groups, falling back to the actor's groups.
2. Deletes reservations and comments and clears expense participants.
3. Sets `shared_list`, clears the reservation group and actual cost.
4. Replaces personal visibility with publications by the actor.
5. Notifies previous reservers by email after commit.

This is a change of recipients; existing purchase coordination is intentionally
cancelled rather than silently carried over.

## Removing members and lists

For lists with more than two members, removing someone removes their publications
and membership and schedules a notification after commit.

For the small-list resolution path (`len(members) <= 2`), the caller chooses
deletion or transfer to the remaining member's personal list. Transfer keeps the
remaining member's publication groups as personal visibility and deletes the
shared-list container. Inspect this branch when changing one-member edge cases.

Deleting a shared list normally sets `deleted_at` and rotates `restore_token`.
Members receive a restoration link; restoration requires membership, the token,
and a POST within 48 hours. The scheduler permanently purges expired lists.

## Implementation and tests

- [shared_lists.py](../../gifts/shared_lists.py): `_active_member_list`,
  `_shared_list_group_context`, `move_gift_to_shared_list`, `_soft_delete`,
  `restore_shared_list`, `_transfer_to_personal_list`.
- [models.py](../../gifts/models.py): `SharedList`, `SharedListMembership`, `SharedGiftPublication`.
- [test_shared_lists.py](../../gifts/test_shared_lists.py).
- [Maintenance](../operations/maintenance.md): permanent purge schedule.
