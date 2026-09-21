# Read, unread, and handing a conversation over

Status: **design only.** Nothing below is built.

The requirement in one sentence: **open your mailbox in Outlook, then open it
in Mail Pro, and see the same thing.** Read state is one fact about a mailbox,
it already exists at the provider, and Mail Pro's job is to show it and to
write to it. Not to keep a second opinion.

## 0. What ships today, and why it cannot work

The Inbox draws a conversation unread when the current user has an unread
`mail.notification` inbox row on its newest message, and the Unread filter is
Odoo's `needaction` search over the same row (`pan.mail.conversation`
`_unread_ids`, `_filter_domain`).

Nothing in this module writes that row. Every imported mail is posted under
`IMPORT_CTX`, and `mail.thread._notify_thread` returns an empty recipient list
for those, so an imported mail produces no notification at all. On a database
whose mail arrives through Mail Pro the Unread filter is close to permanently
empty and the dot close to permanently off.

It is also the wrong fact. A `mail.notification` row is per Odoo user and says
"does this Odoo notification still want me". Outlook's `\Seen` is per mailbox
and says "has this mailbox read this mail". Reading the first and calling it
the second is why the two screens can never agree.

Meanwhile all three clients already fill `is_read` in the normalized message,
from `isRead`, `\Seen` and the absence of the `UNREAD` label, and
`pan_mail_fetcher` throws it away.

## 1. The provider owns the fact

Read state lives at the provider. Odoo keeps a **mirror**, refreshed from the
provider and written through to it, and the mirror is never the authority: on
any disagreement the provider wins, because that is where Outlook, the phone
and Mail Pro all meet.

That settles the question a per-user flag would raise. A mailbox has one read
state, shared by everyone who opens it, because that is what `\Seen` is. On a
personal mailbox there is one person anyway; on a shared mailbox it is the
whole point, since four private unread flags is four people answering the same
mail. **Per-user read state is dropped.** It cannot be mirrored from anything.

## 1a. Odoo's own read state stays Odoo's

Odoo already has a read state and we are not replacing it, syncing it, or
reading it here. They answer different questions and both answers are right:

| | Odoo's `mail.notification` | Mail Pro's dot |
|---|---|---|
| Question | does this Odoo notification still want *me* | has this *mailbox* read this mail |
| Scope | one user | one mailbox, everyone who opens it |
| Set by | a mention, a record you follow | the provider |
| Cleared in | Discuss, the bell | Outlook, the phone, or Mail Pro |

So the rules are:

- **The Inbox stops reading `needaction`.** It never described the mailbox.
- **Mail Pro never writes a `mail.notification` row.** The sync suppresses
  notifications on purpose and that boundary does not move.
- **Marking a conversation read in Mail Pro does not clear your Odoo Inbox.**
  Opening a record in Odoo does not clear it either, so this is Odoo's own
  behaviour left alone rather than a decision of ours. Your mention is yours,
  and a colleague reading the shared mailbox is not you.
- **No mentions filter in the Inbox.** That screen exists, it is Discuss.

The two can therefore disagree on one message: unread in the mailbox and
already ticked off in your Odoo Inbox, or the reverse. That is not drift, it is
two facts. On a database whose mail arrives through Mail Pro the overlap is
rare anyway, because only messages Odoo itself notified have a row at all.

## 2. Why the mirror exists at all

`x_is_read` on `mail.message`, boolean, default `True`.

Not a cache for speed: the list has to filter, sort and page on read state, and
you cannot paginate a database query against a set held in Python. The Unread
filter becomes a clause on the message, which is what `_filter_domain` already
assumes. `_unread_ids()` reads the same column, so the dot and the filter stay
one fact.

Seeded at import from the `is_read` the fetcher currently drops, so a mail you
already read in Outlook arrives read.

## 3. Refreshing it: one cheap call, not one per message

"Which messages are unread" is a single cheap query on all three providers,
because unread is a small set:

| Provider | The call | Comes back as |
|----------|----------|---------------|
| Microsoft 365 | `/messages?$filter=isRead eq false&$select=id` | Graph ids |
| Gmail | `messages.list?q=is:unread` | Gmail ids, no per-message get |
| IMAP | `UID SEARCH UNSEEN` | UIDs |

So: a new contract method **`unread_message_ids(account, mailbox, folder)`**,
returning provider handles and nothing else. Not `search_messages(unread_only=True)`,
which exists but normalizes every hit, and on Gmail costs one metadata call per
message.

Matching those handles to our messages needs the handle stored at import.
Today only the RFC Message-ID survives, in `pan.mail.message.ref`. So
**`provider_message_id` goes on that ref row**, which is already the one place
a wire id for a message lives, and this becomes a set intersection with no
extra header fetch. This is required for the feature, not a later phase.

**When it runs:** when the Inbox opens a mailbox and when the conversation list
refreshes, with a short TTL per mailbox so clicking around does not hammer the
provider, and on every sync cron run. Not per rendered row, not per message.

**Named and dropped:** the refresh asks for the newest N unread (500) and
mirrors only those. A mailbox that keeps five thousand unread mails gets the
newest five hundred right, and the reader who has five thousand unread mails is
not reading them.

## 4. Writing it back

Marking read in Mail Pro sets the mirror and calls `set_seen`, which already
exists on the contract and in all three clients with no caller.

- One RPC, `set_read(model, res_id, read)`, per conversation. Nobody reads the
  fourth message of a thread and not the fifth.
- In the list: the row menu, and `u` on the keyboard. No setting.
- Off the request: a 40 message conversation is one Odoo write and one queued
  provider call, not forty round trips while the user waits.
- **Best effort, and it never rolls back the Odoo side.** The contract already
  says marking is the one mail write that undoes itself. A provider that is
  down makes the two disagree until the next refresh, which the next refresh
  fixes, because the provider is the authority.

## 5. Handoff is not read state

Read state cannot hand work over. It has nobody's name on it, it undoes itself,
and the colleague you meant to hand it to has no reason to look. Using unread
as a shared to-do list is the classic shared mailbox failure and it fails the
same way here.

A handoff needs an owner and a question, and Odoo has both. The conversation is
linked to a record, the record takes activities, and the Inbox already shows an
Activities tab over it.

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

1. `provider_message_id` on the ref index, written at import.
2. `x_is_read`, seeded at import, read by the dot and the filter in place of
   `needaction`.
3. `unread_message_ids()` on the contract and in the three clients, plus the
   refresh on opening a mailbox. **After this step the two screens agree**,
   which is the feature.
4. The toggle and the write-through to `set_seen`.
5. Hand over.

Steps 1 to 3 are one release and they are the whole requirement: what Outlook
knows, Mail Pro shows. Step 4 makes it work in the other direction too. Step 5
is independent of all four and could go first if the handoff is what hurts.
