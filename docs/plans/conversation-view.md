# The conversation view

Status: proposal. Canvas: https://claude.ai/artifact/KsLjy3wNcx7q3dX34Gh3hB

The survey says two thirds of respondents already send from inside Odoo and
that what they cannot do is see a customer's correspondence in one place.
Odoo's chatter is record-centric; mail is conversation-centric. This is the
smallest object that closes that gap.

## The decision

**The conversation is a new object, and the contact is where it is read.**

A conversation groups `mail.message` rows that already exist. It does not copy
them, so every message keeps the access rights of the record it sits on and the
queue removed in 19.0.7.0.0 does not come back in another shape. Threading is
the `References` chain, with the provider's thread id as the fallback the
matcher already uses.

Three screens, in this order:

1. **Conversations tab on the contact.** On a company it covers everyone who
   works there. This is the primary surface and the reason not to build an app:
   "what is going on with this customer" is asked from the customer.
2. **The conversation reader.** The thread with quoted history collapsed, the
   records it touches in a right rail, and a reply that goes out through the
   right mailbox with the conversation quoted underneath. That last part is a
   survey complaint in its own right: today each chatter reply reaches the
   customer as a standalone mail.
3. **Waiting on us.** The same list without the contact filter and with one
   saved filter: the last message is inbound and nothing went back. This is the
   cheap answer to "what is coming in".

## What it reuses

- `pan_mail_matcher` decides which record a message belongs to. Unchanged.
- `pan_mail_routing_log` already records why it landed there and what was
  rejected. The reader shows it rather than re-deriving it.
- Odoo's own message rights do the filtering. Read `mail.message` as the user,
  never as sudo: a message on a record they cannot open simply is not there.

## What we are not building, and why

- **Folders, labels, snooze.** That is a mail client, and they already have one.
- **Read state synced back to the provider.** Expensive, fragile, and it drifts
  the moment a sync is late. "Waiting on us" is derived from the messages
  instead, so it cannot be wrong.
- **A shared team inbox.** Missive and Chatwoot own that category and three of
  the 28 respondents have already bought one. Competing there is the expensive
  half of the problem and the half Odoo has no advantage in.
- **An app tile.** 19.0.7.0.0 removed one for good reasons. The tab earns the
  daily traffic first; a menu entry for the global list can follow if people ask
  for it.

## Open questions

- Where the global list lives before it earns a menu: under Settings →
  Technical → Email → Mail Pro with the other screens, or nowhere yet.
- Whether a conversation may span two records (a quote and the ticket that came
  out of it) or whether that is two conversations sharing participants.
