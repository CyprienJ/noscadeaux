# Groups, invitations, and managed people

[Features](README.md)

A group connects accounts so they can share wishes and coordinate gifts. An
account can belong to several groups. The group stores a creator, but invitation
management currently uses membership rather than an owner/admin role hierarchy.

## Two ways to join

| Identifier | Purpose | Implementation |
| --- | --- | --- |
| `group_token` | Short code entered manually | Generated as eight uppercase hexadecimal characters; case-insensitive lookup |
| `invitation_token` | Private link to copy or email | Generated with `secrets.token_urlsafe(32)`; exact lookup |

The two tokens are independent. Regenerating a private link invalidates previous
private links without replacing the short code. Keep their routes and pending
invitation representations distinct.

Both entry points use the same invitation flow. A non-member sees a limited
preview: group name, image, and member count. The member list is reserved for
people already in the group. Joining requires an explicit POST; opening a link
alone does not add membership. Incomplete accounts resume setup first.

Invalid or revoked pending links are cleared. Joining clears the matching pending
invitation; an unrelated pending invitation is retained. Creating a group or
skipping the group step completes setup and clears the pending invitation.

## Invitation screen and email

After group creation, the invitation screen provides copy/share controls, link
rotation, email invitations, and managed-person creation. Each email recipient
gets a separate message. Addresses are normalized and deduplicated before sending.
Links created from the screen or its email action use the request's host/protocol;
calls without a request fall back to `PUBLIC_BASE_URL`.

Quotas count requested recipients, including failed deliveries. The service locks
the sender and group while reserving quota and records requested/sent/failed
counts in `GroupInvitationDispatch`; it does not retain recipient addresses there.
Delivery failures can be partial and are shown as such.

See [configuration](../operations/configuration.md) for limits and
[external services](../architecture/external-services.md) for email behavior.

## People without accounts

Use a managed person for someone whose list the group maintains, such as a child.
Creation needs a trimmed name of 1–100 characters and creates:

1. An inactive `User` with `is_managed=True`, an internal email, and an unusable password.
2. A linked `ManagedMember` containing the name, group, and display color.
3. Membership of the technical user in that group.

Active, non-managed members in the same demo scope can manage these people.
Renaming updates both the profile name and technical user's nickname. A managed
person's wishes use the ordinary list machinery and are displayed as wishes,
even when another group member created them. Claiming a managed profile as a real
account is not currently implemented.

## Deletion behavior

Deleting a managed profile removes its inactive technical user and associated
gifts through model cascades and signals. Deleting a group removes its managed
people but does not delete real member accounts. A group is removed when its last
non-managed member leaves or is deleted; deleting only its creator need not remove
the group while other real members remain.

Signals distinguish user-initiated cascades from profile deletion to avoid
recursive deletion. Keep that distinction when changing cleanup behavior.

## Implementation and tests

- [groups.py](../../gifts/groups.py): route handlers and group pages.
- [group_invitation_flow.py](../../gifts/group_invitation_flow.py): preview/join/dismiss decisions.
- [group_invitations.py](../../gifts/group_invitations.py): email and quotas.
- [group_permissions.py](../../gifts/group_permissions.py): invitation and people policies.
- [managed_members.py](../../gifts/managed_members.py), [signals.py](../../gifts/signals.py).
- [test_group_invitations.py](../../gifts/test_group_invitations.py),
  [test_managed_members.py](../../gifts/test_managed_members.py).
