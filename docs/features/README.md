# Features and workflows

[Project overview](../../README.md)

nosCadeaux separates the person receiving a gift from the people preparing it.
Groups define social circles; lists hold wishes; reservations coordinate buyers.
Some screens deliberately hide surprises and reservation details from recipients.

## Choose a workflow

| Topic | Read when working on |
| --- | --- |
| [Accounts](accounts.md) | Registration, email verification, onboarding, profiles, demo access |
| [Groups and managed people](groups.md) | Membership, invitations, children or relatives without accounts |
| [Lists and wishes](lists.md) | Personal wishes, drafts, surprises, tags, list visibility |
| [Events](events.md) | Public event links, guest reservations, Secret Santa |
| [Notifications](notifications.md) | Subscriptions, RSS, digests, birthday and Christmas reminders |

The lists guide branches further into [shared lists](shared-lists.md) and
[reservations, history, and balances](reservations.md).

## A typical journey

Alice verifies her account, completes her profile, and creates a family group.
She shares an invitation with Bob and adds a managed profile for a child. Each
person has a gift list. Bob can reserve one of Alice's wishes or add a surprise
for her. Alice's own list view keeps preparations hidden from her.

Alice and Bob can also manage a shared list, such as wishes for their household.
They publish shared wishes into their respective groups. For a one-off celebration
with visitors who do not have accounts, an event list provides a separate access
and reservation flow.

## Terms used throughout the documentation

| Term | Meaning |
| --- | --- |
| Wish / gift | A `Gift` record: an idea, not necessarily something purchased |
| Owner / recipient | The account receiving a personal gift |
| Creator | The account that entered the gift; may differ from the recipient |
| Managed person | A group profile backed by a technical, inactive user |
| Shared-list member | A person who manages the shared list and is treated as a recipient |
| Reservation | A buyer's participation in preparing a gift |
| Offered | A gift moved into the history/expense workflow |
| Publication | A shared wish exposed to a group by one shared-list member |

For the implementation map, continue to [Architecture](../architecture/README.md).
