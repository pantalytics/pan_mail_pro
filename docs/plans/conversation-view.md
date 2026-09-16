# The conversation view

Status: proposal. Canvas: https://claude.ai/artifact/KsLjy3wNcx7q3dX34Gh3hB

The survey says two thirds of respondents already send from inside Odoo and
that what they cannot do is see a customer's correspondence in one place.
Odoo's chatter is record-centric; mail is conversation-centric. This is the
smallest object that closes that gap, in the layout people already know.

## The decision

**The conversation is a new object, and it is read in an inbox.**

A conversation groups `mail.message` rows that already exist. It does not copy
them, so every message keeps the access rights of the record it sits on and the
queue removed in 19.0.7.0.0 does not come back in another shape. Threading is
the `References` chain, with the provider's thread id as the fallback the
matcher already uses.

The screen is four panes:

1. **Folders.** The mailbox you are in, plus the states that are worth their own
   entry: needs reply, waiting on customer, sent, and the two unfiled ones (on a
   contact only, linked to nothing). Shared mailboxes below your own.
2. **The conversation list.** Sender, subject, snippet, date, and the record the
   thread is filed on. Unread is weight, not a badge.
3. **The thread.** Messages in order, quoted history collapsed, and a reply that
   goes out through the right mailbox with the conversation quoted underneath.
   That last part is a survey complaint in its own right: today each chatter
   reply reaches the customer as a standalone mail.
4. **The record.** The quote, ticket or invoice the thread is filed on, with its
   chatter. This is the pane a mail client cannot have, and the reason to read
   mail here rather than in Outlook.

When nothing is linked, the fourth pane shows what the matcher considered and
rejected, with a one-click way to file it. An unfiled conversation is the case
that decides whether people trust the screen, so it gets a designed state rather
than an empty panel.

An earlier draft put this on a tab of the contact form. It was rejected in
review for the right reason: nobody recognises that interface. The customer view
is still reachable, as the same list filtered to one company.

## What Odoo already has

[docs/research/odoo-mail-surfaces.md](../research/odoo-mail-surfaces.md) reads
Discuss, notifications and activities against the 19.0 source. In short: Odoo
has a notification queue you empty, a chat client, and a to-do list a person
fills in by hand. Nothing groups messages into a conversation, nothing shows a
customer's correspondence, and nothing knows that a customer is waiting for an
answer. Two consequences are load-bearing here: a follow-up with a date on it
is a `mail.activity` on the linked record, never a queue of our own, and this
app adds no systray counter next to the two Odoo already has.

## The interplay that does not work yet

Companies run Odoo and a mailbox side by side, and the two do not agree about
who is in a conversation. Odoo keeps followers, a list of people to notify
internally. Email keeps To and Cc, a list the customer can see. Odoo treats
them as one list, and both survey complaints follow from that:

- **"The automatic adding of followers creates complications."** `message_post`
  subscribes the recipients when `mail_post_autofollow` is in the context,
  subscribes the customer when the model asks for it, and subscribes the author
  besides. So being cc'd on one mail signs you up for everything that record
  ever does.
- **"Geen cc zichtbaarheid bij ontvanger, veroorzaakt veel verwarring."** The
  other direction: the customer cannot see who else is on the thread, because
  the people are followers rather than addressees.

**Position: the reply's recipients come from the conversation, the followers
stay the record's.** Who was on the last message decides the To and Cc; the
follower list decides who gets an internal notification; the reply header shows
both, separately, before you send. Nobody becomes a follower because they were
cc'd once.

This is not a fight with the framework. Odoo 19's `message_post` already takes
`outgoing_email_to` alongside `partner_ids`, described in its own docstring as
experimental, which is the same separation arriving from the other side.

## Why this is not a second source of truth

The screen is a projection. It stores no fact about the mail, the record or the
work, and every action it offers writes through a door Odoo already has, so the
chatter, Discuss and the Activities clock cannot disagree with it.

"Stores nothing" would be the wrong rule, and it is worth being exact about
why. The test is not whether a row exists, it is whether a row could ever
contradict Odoo. Two things fail that test in opposite directions: a derived
index cannot contradict anything because it is recomputable, and a per-person
read pointer cannot contradict anything because it says where someone's eyes
were, not what happened. Everything else stays out.

| What the inbox shows | Where the truth lives | How the inbox writes to it |
|---|---|---|
| The messages | `mail.message`, on the record they were filed on | Never writes. The conversation is a grouping key, not a copy |
| Unread, where Odoo knows it | `mail.message.needaction`, the same row Discuss reads | Marking read calls `set_message_done()`, so Discuss un-bolds too |
| Unread, everywhere else | A read pointer per person per conversation, ours. See below | Moves when you open the conversation |
| Flagged | `starred_partner_ids` | `toggle_message_starred()`, which is Discuss's own star |
| Waiting on us | Nowhere. Derived: the last message is inbound and nothing went back | Never writes. It cannot drift because it is recomputed from the messages every time |
| A reply | `mail.message` again, through `message_post()` on the linked record | The chatter shows it, the followers get it, `mail.mail` sends it through the provider, the routing log records it |
| A follow-up with a date | `mail.activity` on the linked record | `activity_schedule()`. It then appears in the Activities clock and on the record, where the salesperson already looks |
| Where a conversation is filed | `mail.message.model` and `res_id`, decided by the matcher | Re-filing writes the same fields and adds a routing-log row saying a person overrode it |
| Participants | The authors and recipients of the messages | Display only. Who gets notified stays the record's followers |

**The one new table is a derived index.** `pan.mail.conversation` holds the
thread key, the participants and the last-message date so the list can sort and
page in SQL. It holds no fact that is not already in `mail.message`, so it can
be dropped and rebuilt from the messages at any time. That is the test it has to
pass in CI: rebuild the index from scratch and assert the same grouping, the
same order and the same waiting-on-us answers. An index that cannot be rebuilt
has become a source of truth, and that failure is silent otherwise.

**The read pointer is the one thing we store, because borrowing does not
work.** `needaction` is a `mail.notification` row for your partner with
`is_read = False`, so a message only counts as unread for the people who were
notified about it. On a shared mailbox that nobody follows there are no such
rows at all, which means that without a pointer of our own every conversation
in `info@` reads as already read, forever. Odoo solves exactly this for its own
conversations: `discuss.channel.member` carries `seen_message_id`,
`new_message_separator` and `last_seen_dt`. We copy that shape, one row per
person per conversation, holding the last message they saw and nothing else.
It is a bookmark, so losing it costs a re-read and cannot make anything wrong.

**The three fields we will be asked for and must refuse**: a per-user read flag
of our own, a per-conversation status (open, closed, resolved), and an assignee.
Every team-inbox product has them, each one is a fact the chatter cannot see,
and together they are how the screen stops agreeing with Odoo. Read state is
Odoo's needaction, status is derived, and the assignee of work is the activity's
`user_id` on the record.

## What it reuses

- `pan_mail_matcher` decides which record a message belongs to. Unchanged.
- `pan_mail_routing_log` already records why it landed there and what was
  rejected. The fourth pane shows it rather than re-deriving it.
- Odoo's own message rights do the filtering. Read `mail.message` as the user,
  never as sudo: a message on a record they cannot open simply is not there.

## What we are not building, and why

- **Folders, labels, rules, snooze.** The provider has them. A second set that
  disagrees with the first is worse than none.
- **Read state synced back to the provider.** Expensive, fragile, and it drifts
  the moment a sync is late. "Waiting on us" is derived from the messages
  instead, so it cannot be wrong.
- **Team-inbox machinery**: assignment, SLA timers, collision detection, a chat
  per thread. That is Missive and Chatwoot, and three of the 28 respondents have
  already bought one. The chatter already holds the internal conversation, one
  pane to the right.

An inbox does earn the app tile that 19.0.7.0.0 took away, by that release's own
test: a tile is a promise about how often a screen is opened, and this one is
opened all day. The diagnostics it removed stay where they are.

## Open questions

- Whether a conversation may span two records (a quote and the ticket that came
  out of it) or whether that is two conversations sharing participants.
- Whether "waiting on us" should also be personal on a shared mailbox, or stay
  one list for the team. The read pointer makes unread personal; waiting-on-us
  is derived from the messages and is therefore the same for everyone, which is
  probably right for a team of three and probably wrong for a team of ten.
