# Where Mail Lands

Syncing email into Odoo is only half the job. The other half is being able to
answer "where did that email go?" — and to notice when the answer is wrong.

Mail Pro gives you four screens for this. Three of them live under the
**Communication** menu; the fourth is in the technical settings.

## All Communication

**Communication → All Communication**

Every email Mail Pro has sent or received, in one list, with the thing standard
Odoo cannot show you: which document each message ended up on, and which mailbox
it came through.

Odoo stores a message's document as a model name and a record id — a pointer
with no foreign key, which cannot be grouped or clicked. And every note, system
log and internal chat lives in the same table as your customer email. So "show
me all mail with this customer" has no answer in a standard database. This
screen is that answer.

Group by mailbox, by direction, or by document to see the shape of your
correspondence.

## Link Coverage

**Communication → Link Coverage**

The single number that says whether any of this is working: of all the mail that
came through, how much actually landed on a document rather than just on a
contact.

Pick a period (30 days, 90 days, a year) and you get three counts:

| Count | Meaning |
|-------|---------|
| **Linked** | Landed on a real document — an order, a lead, a ticket |
| **Contact only** | Landed on the contact's chatter and nowhere more specific |
| **Unlinked** | Did not land on anything |

A high "contact only" ratio usually means aliases are not configured: mail is
arriving safely but nobody is looking at it, because nothing was created for
anyone to work on. See [Incoming Email Sync](incoming-sync.md) for routing to a
team.

This report is calculated inside your own database and nothing about it is sent
anywhere. It is a question you ask, not a history that is stored, so the numbers
cannot go stale.

## Triage

**Communication → Triage**

Mail that reached Odoo but was not filed anywhere — most often an email from a
sender who is not yet a contact, on a mailbox configured to hold those for
review.

From an item you can create the contact, file the mail where it belongs, or
discard it.

**What is deliberately *not* here.** Mail that was filtered on purpose never
becomes a triage item — not even its metadata:

- A contact you blocked. Blocking is an objection to processing that mail;
  storing it in a new table would invert what the setting means.
- Mail between colleagues. It has no document context and no document
  permissions to inherit.
- Mail from your own internal domains. You excluded it by configuration.

The queue also stores no bodies and no attachments. The item is metadata; the
body is fetched from your mail provider when you open it. That keeps a second
copy of your email out of the database.

## Mail Routing log

**Settings → Technical → Email → Mail Routing**

One row per delivered email, recording which rule placed it, how confident that
rule was, and every candidate it considered and rejected.

This is a record of what happened, **not** a queue that holds mail back.
Delivery is never delayed by it. A log that is wrong costs you a confusing row;
a queue that is wrong costs a customer an answer.

The **outcome** column separates three things that look identical from inside
Odoo:

| Outcome | Meaning |
|---------|---------|
| `threaded` | Attached to a conversation that already existed |
| `created` | A new record was opened for it |
| `fallback` | Delivered to the contact's chatter — nothing better was found |
| `sent_item` | Our own outgoing mail, synced back |

**Needs review** flags exactly two of these, and it is worth understanding why:

- **fallback** — delivered, but to a place nobody is looking.
- **created *with* rejected candidates** — the expensive, silent one. We may
  have opened a duplicate ticket for a conversation that was already running.

A `threaded` mail and our own sent items never flag. A review queue that cries
wolf on every routed email gets ignored, and then it may as well not exist.

Rows are cleaned up automatically after 90 days unless they are still flagged.
An administrator can change that with the `pan_mail_pro.routing_log_retention_days`
system parameter.

## When replies land on the wrong record

Mail Pro decides where a reply belongs by running rules strongest-first and
stopping at the first confident answer:

1. **Odoo's own headers** — mail we sent carries the record it came from
2. **The `References` chain** — the standard email threading headers
3. **The provider's thread id** — `conversationId` on Microsoft,
   `threadId` on Gmail, scoped to the mailbox that saw it
4. **Subject and participants** — only ever a suggestion, never acted on alone

If a reply lands somewhere unexpected, open its row in Mail Routing: the rule
that placed it and the candidates it passed over are both recorded there.

The most common cause is that the original message was never synced, so there
was nothing to thread onto. Check the sync start date on the mailbox — see
[Incoming Email Sync](incoming-sync.md).
