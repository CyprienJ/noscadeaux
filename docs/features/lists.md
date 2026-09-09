# Lists and wishes

[Features](README.md)

A wish is stored as a `Gift`. The same model supports several workflows, so its
recipient, creator, and list relationships must be interpreted together.

## List types

| Type | Who manages it | Detail |
| --- | --- | --- |
| Personal | The recipient; other group members can suggest surprises | This page |
| Managed person | Eligible members of the person's group | [Groups](groups.md) |
| Shared | Several shared-list members | [Shared lists](shared-lists.md) |
| Event | Event owner, with separate guest participation | [Events](events.md) |

## Personal wishes and surprises

`owner` identifies the recipient and `created_by` identifies who entered the idea.
A normal wish has the same creator and owner. A group member can add an idea for
someone else; it is treated as a surprise and omitted from the recipient's own
list view. Managed-person lists are an exception: those ideas appear as wishes.

Personal wishes can have a title, description, product URL, tags, and selected
groups. The model also holds price, currency, and image URL, used by product
capture and other gift actions. These fields are not all entered by the same form.

A personal owner can save a draft. Drafts are omitted from visitor lists and from
new-gift notifications. Offered gifts leave the active list and enter history.
Event gifts are kept separate from the ordinary personal-list section.

## Visibility and navigation

`visible_in` stores explicit group selections for personal gifts. Empty selection
is used as unrestricted sharing within eligible access paths, not as a private
draft flag. Use `is_draft` for draft behavior.

The list route supports `from_group` to select group context, tag filters, and
oldest/newest/title ordering. In group context, visitor gifts are filtered by
the selected group or empty visibility. Opening another person's list first
requires common membership or a qualifying Secret Santa assignment.

Visibility checks are distributed between list rendering, reservation/comment
helpers, feeds, and notification code. Consult the exact path being changed;
a queryset suitable for one surface is not automatically correct for another.
See [access control](../architecture/access-control.md).

## Gift preparation

Buyers use reservations and private group comments while the recipient's view
hides preparation details. Shared-list members receive the same protection for
their shared wishes. Offering and receiving actions connect the list to history
and optional group expense tracking.

Continue to [reservations, history, and balances](reservations.md) for those rules.

## Implementation and tests

- [models.py](../../gifts/models.py): `Gift`, `GiftTag` and list relationships.
- [views.py](../../gifts/views.py): `view_list`, `add_gift`, `edit_gift`, `delete_gift`.
- [view_list.html](../../gifts/templates/gifts/view_list.html): sections and interactions.
- [tests.py](../../gifts/tests.py): `GiftAccessControlTest`, `DashboardGiftCountTest`.
- [Extension protocol](../architecture/extension.md): adding a reviewed shop product.
