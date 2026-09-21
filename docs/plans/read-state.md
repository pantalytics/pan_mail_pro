# Read, unread, and handing a conversation over

Status: **design only.** Nothing below is built. Two decisions are made here
and the code is a consequence of them: read state is a property of the
*mailbox*, not of an Odoo user, and handing work to a colleague is not
something read state can do.

## 0. What ships today, and why it barely works

The Inbox draws a conversation unread when the current user has an unread
`mail.notification` inbox row on its newest message, and the Unread filter is
Odoo's `needaction` search over the same row (`pan.mail.conversation`
`_unread_ids`, `_filter_domain`). One fact, read in two places, which was the
right call for a screen that could only read.

The problem is what writes it. Nothing here does. Every mail the sync imports
is posted under `IMPORT_CTX`, and `mail.thread._notify_thread` returns an empty
recipient list for those, so an imported mail produces no notification row at
all. On a database whose email arrives through Mail Pro, the Unread filter is
therefore close to permanently empty and the dot close to permanently off. The
feature is not missing a toggle. It is reading a fact nobody in this module
ever sets.

The providers, meanwhile, all hand us the answer and we throw it away:
`is_read` is in the normalized message shape, all three clients fill it from
`isRead`, `\Seen` and the `UNREAD` label, and `pan_mail_fetcher` never looks
at it.

## 1. The decision: read state belongs to the mailbox

A mailbox has one read state, shared by everyone who opens it. That is what
`\Seen` means, it is what Outlook shows a second person on `support@`, and it
is the only version that makes a shared mailbox workable: if four people each
carry a private unread flag, four people each answer the same mail.

**The case being dropped: per-user read state.** On a personal mailbox there is
one person, so the distinction is free. On a shared mailbox it is actively
harmful. There is no third kind of mailbox, so the case is not worth a setting.

This replaces `needaction` as the source. Odoo's own inbox keeps answering its
own question, which is "does this Odoo notification still want me", and it is
a different question from "has this mailbox read this mail". Two facts, two
places, neither pretending to be the other.

## 2. The toggle (part one, shippable alone)

- `x_is_read` on `mail.message`, boolean, default `True`. Everything already
  in the database reads as read, which is exactly what it reads as today.
- The fetcher writes it from the provider's `is_read` at import, so a mail you
  already read in Outlook arrives read.
- `_unread_ids()` and `_filter_domain('unread')` read `x_is_read`. The filter
  becomes a clause on the message, which is what that method already assumes.
- One RPC, `set_read(model, res_id, read)`, marking every message of the
  conversation. Marking is per conversation because reading is: nobody reads
  the fourth message of a thread and not the fifth.
- In the list: the row menu, and `u` on the keyboard. Nothing else, and no
  setting.

## 3. Write-through to the provider (part two)

The Inbox marks a mail read; Outlook should agree. `set_seen` already exists on
the contract and in all three clients with no caller. It needs the provider's
own handle for the message, which is the one thing we do not store: the ref
index keeps the RFC Message-ID (`pan.mail.message.ref`), and the provider
handle survives only on the thread link's `last_provider_message_id`.

So: **add `provider_message_id` to `pan.mail.message.ref`.** That table is
already the one place a wire id for a message lives, which is the rule
19.0.6.0.0 settled on after three copies of one id drifted apart. A row that
has no handle, or an IMAP handle the server renumbered, falls back to the
Message-ID search each client already supports.

Two properties this write must have:

- **Best effort, and it never rolls back the Odoo side.** The contract already
  says marking is the one mail write that undoes itself. A provider that is
  down must not make the button not work; it makes the two disagree until the
  next refresh, and disagreeing about read state is not a data loss.
- **Off the request.** Marking a 40 message conversation read is one Odoo write
  and one provider call, queued, not forty round trips while the user waits.

## 4. The refresh (part three, and the honest limit)

The other direction is what makes this feel alive: read it in Outlook, the dot
goes out in Odoo. Doing that properly is a per-provider change stream (Graph
delta, Gmail history, IMAP flag fetch) and its own cursor, and that is a larger
feature than this one.

The cheap 90%: the incoming sync already lists the Inbox and the Sent folder
over its window, and every one of those listings already carries the read flag.
Refresh `x_is_read` for the messages in that listing that we have already
imported. No extra API call, no new cursor, bounded to the window the sync
already looks at.

**Named and dropped: mail older than the sync window does not get its read
state refreshed.** It keeps whatever Odoo last knew. A three week old thread
whose dot is stale is not the complaint this feature exists to answer.

## 5. Handoff is not read state

Read state cannot hand work over. It has nobody's name on it, it undoes
itself, and the colleague you meant to hand it to has no reason to look. Using
unread as a shared to-do list is the classic shared mailbox failure, and it
fails the same way here.

What a handoff needs is an owner and a question, and Odoo has both already.
The conversation is linked to a record, the record takes activities, and the
Inbox already shows an Activities tab over it. So:

- **Hand over** on the conversation: schedule an activity on the linked record,
  assigned to the chosen user, summary prefilled from the subject. It lands in
  their Odoo to-do list, where they already look, and it is done when they mark
  it done.
- No new model, no assignee field on a conversation, no queue. The queue
  removed in 19.0.7.0.0 was this idea in another shape.
- **Dropped: handing over a conversation linked to nothing.** Link it first.
  Triage is already the step before this one.

An "Assigned to me" list filter is the obvious next thing and is deliberately
not in this plan. It earns itself once Hand over is used.

## 6. Order

1. `x_is_read`, seeded at import, read by the list and the filter, toggled from
   the row. This alone is the feature people asked for.
2. The refresh on the sync listing, which is small and makes part 1 stop lying.
3. `provider_message_id` on the ref index and the write-through to `set_seen`.
4. Hand over.

Parts 1 and 2 are one release and do not touch a provider. Part 3 is where the
provider work is. Part 4 is independent of all three and could go first if the
handoff is the thing that hurts.
