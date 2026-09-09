# Reservations, history, and balances

[Lists and wishes](lists.md)

Personal and shared wishes use `Reservation`. Event lists use a separate
`GuestReservation` workflow described in [Events](events.md).

## Preparing a gift

A reservation records a participating user, whether the reservation is exclusive,
and an optional amount paid. A gift's `group_reserved_on` identifies the group
coordinating it. Views distinguish availability, open participation, exclusivity,
the current person's participation, and reservation in another group.

Creation validates the selected group and participants. Personal recipients and
shared-list members cannot reserve their own gifts. A participant already on the
gift is rejected, as is joining a gift with an existing exclusive reservation.
Actions in another group must respect the existing reservation group.

The application can return rendered modal fragments from these actions; they
are not uniformly JSON-only endpoints. Consult the individual handler before
changing a browser caller.

## Comments and privacy

`GiftComment` belongs to both a gift and a group. Comments support editing and
soft deletion through `is_deleted`, with deletion metadata retained. Permission
checks consider the group, gift visibility, and whether the viewer is a recipient.

Recipients' personal-list views and shared-list members' views hide buyer
coordination. Avoid passing reservation or comment data into a recipient view
and merely relying on a hidden HTML control.

## Offered and received gifts

Offering marks a gift as offered, records timing and purchase information, and
moves it out of active wish lists. History has personal and group views. Further
actions can change payer amounts, undo offering, remove offered gifts, or mark a
gift received. These paths have distinct checks; `_check_gift_access` alone is
not a complete policy for every action.

Groups store `show_history` and `show_balance` display preferences. Expenses use
`actual_cost`, `expense_split`, reservation `amount_paid`, and settlements.

## Balance calculation

`compute_group_balances()` considers offered gifts assigned to the group with a
nonzero actual cost. For each gift with expense participants, it subtracts an
equal share of the cost from each participant and credits the amounts paid by
reservers. A settlement credits its payer and debits its payee.

The result includes per-member net balances and suggested debtor-to-creditor
transfers. Calculations use `Decimal`; displayed values are rounded to cents.
Unmatched debt or credit can appear without a counterpart when recorded payer
amounts and expense shares do not balance. These are records and suggestions;
the application does not execute payments.

## Implementation and tests

- [views.py](../../gifts/views.py): `_get_reservation_group`,
  `_validate_reservation_participants`, `_reservation_state`, `_get_comment_group`,
  `offer_gift`, `mark_received`, `compute_group_balances`.
- [models.py](../../gifts/models.py): `Reservation`, `GiftComment`, `BalanceSettlement`.
- [tests.py](../../gifts/tests.py): `ReservationFlowTest`, `GiftCommentTest`,
  `OfferGiftTest`, `HistoryViewTest`, `ComputeGroupBalancesTest`, `BalanceViewTest`.
