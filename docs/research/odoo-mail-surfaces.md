# What Odoo already gives you: Discuss, notifications, activities

Read against Odoo 19.0 source (`addons/mail`) and the models present on
pantalytics.odoo.com, September 2026. The question behind it: does
[the conversation view](../plans/conversation-view.md) duplicate something that
ships already?

Short answer: no. Odoo has a notification queue, a chat client and a to-do
list. It has no mail client, and nothing anywhere groups messages into a
conversation.

## Discuss

`mail.Discuss` is two panes: a sidebar and the content, and the content is a
header, a `Thread` and a `Composer`. A third panel exists but it holds chat
things, the member list, attachments, pinned messages, call settings. Nothing
in Discuss puts a business record next to a message.

**Inbox, Starred and History are not folders.** They are pseudo-threads on the
`mail.box` model, and the composer is hidden on them unless you press reply on
a single message (`thread.model !== 'mail.box' or thread.composer.replyToMessage`).

| Mailbox | What is in it |
|---|---|
| Inbox | Messages where you have a `mail.notification` row, meaning you were notified as a follower or a mention (`mail.message.needaction`) |
| Starred | Messages you flagged, through `starred_partner_ids` |
| History | The ones you already marked done |

So the Inbox is a list of things that want your attention once. You empty it,
and `set_message_done` removes the needaction for good. That is the opposite of
a mailbox, which is correspondence you keep and come back to.

What it does not do, and would have to grow to do:

- **No conversation.** Messages are listed one by one, newest first, each with a
  small "on *document*" link back to the record. `mail.message` carries
  `parent_id`, `message_id` and `reply_to`, so the material for threading is
  there, but no screen groups by it. Odoo threads by record, never by
  conversation.
- **No customer view.** You cannot ask it what a company has been talking to you
  about; there is no grouping by partner at all.
- **Nothing outgoing.** The Inbox holds what was addressed to you. Your own sent
  mail is not there, so half of every conversation is missing.
- **No delivery state and no waiting.** Both exist in the data
  (`mail.notification.notification_status` is what the module's
  `x_delivery_state` reads) and neither is a filter you can open.

## Activities

`mail.activity` is the to-do list: `res_model` and `res_id` for the record,
`activity_type_id`, `date_deadline`, `user_id`, `summary`, and a `state` of
overdue, today, planned or done. The systray clock groups them by model and
counts Late and Today.

The thing to notice: **an activity is always created by a person.** Nothing in
Odoo turns an unanswered email into one. Mail arrives, notifies the followers
once, and then sits on the record unless somebody decides to act. That gap is
exactly where "this customer mailed on Monday and nobody replied" lives, and it
is why the survey has three separate people describing mail that arrives safely
and is then invisible.

## Notifications

`mail.notification` is one row per recipient per message: `notification_type`
(inbox or email), `notification_status` (ready, sent, bounce, exception,
canceled). It drives the Inbox counter and it is where a bounce is recorded.
It is per message, never per conversation, and it is deleted along with the
needaction.

## What this means for the conversation view

1. **We are not duplicating Discuss, and we must not look like it.** Users read
   Discuss as chat. The proposal is a mail client, and the pane that makes it
   worth opening, the record with its chatter, is a thing Discuss structurally
   does not have.
2. **Do not invent our own follow-up.** When a conversation needs one with a
   date on it, create a `mail.activity` on the linked record. That is the queue
   salespeople already look at, and a second one competing with it would be the
   triage queue mistake again, in a new shape.
3. **Waiting on us fills a real hole.** Needaction disappears when read and an
   activity has to be created by hand, so today nothing in Odoo knows that a
   customer is waiting. Deriving it from the last inbound message needs no new
   state and cannot drift.
4. **No third counter in the systray.** Odoo already has the Discuss envelope
   and the Activities clock. A third badge is how people stop reading all
   three. The app tile is the entry point.
5. **Naming.** Odoo's Inbox already means something specific and it is not ours.
   Inside the app the folders are named after the mailbox they belong to.
