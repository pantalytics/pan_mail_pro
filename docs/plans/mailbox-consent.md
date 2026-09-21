# Consent: who decides what a personal mailbox shares

Status: **design only**, nothing below is built. It is about the one question
the sync ladder (ARCHITECTURE.md §3) does not answer: the rungs are right, but
the wrong person climbs them.

## What is wrong today

Connecting a mailbox is one click and creates a personal mailbox at `replies`,
the bottom rung. That part is correct and stays.

Everything after that belongs to somebody else. `sync_level` is writable by
`group_mail_mailbox_manager` on *any* personal mailbox; the owner has
`1,0,0,0` on `pan.mail.mailbox` and sees only their own row. So an
administrator can put a colleague's whole inbox, new conversations and Sent
folder included, into the database, and the colleague has no screen that says
so and no field to set back. A control built as a privacy boundary is held by
the one party whose privacy is not at stake.

The second half is not fixable and has to be said out loud instead. Once a mail
is on a record, Odoo shows it to everyone who can read that record. There is no
per-message ACL in chatter and we are not building one. The consequence of a
rung is therefore permanent and public within the company, which is exactly why
the choice has to be the owner's and has to be made where the consequence is
written down.

## The decision

**A personal mailbox's rung belongs to its owner.** Three changes, and the
third is the one that makes it real.

1. **Only the owner raises the rung.** A field-level guard in
   `pan.mail.mailbox.write()` -- a record rule cannot express "this field" --
   refusing a raise of `sync_level` on a personal mailbox by anyone but
   `owner_user_id`. A manager may still *lower* it, archive the mailbox and
   disconnect the account: stopping a sync is never blocked, starting one is.
   Shared and notification mailboxes are unchanged and stay the manager's.

2. **Ask once, at connect, on its own screen.** The OAuth callback comes back
   to one page with the four rungs, `replies` preselected, one line each saying
   who ends up seeing the mail. Closing the page leaves `replies`. That screen
   is the only place in the product where the sentence "mail that lands on a
   record is visible to everyone who can open that record" appears, because it
   is the only moment where it can still change a decision.

3. **One line the owner can always find.** In Preferences and at the top of
   their mailbox in the Inbox: what Odoo currently reads, in the rung's own
   words, with Change and Stop next to it. Stop drops to `replies`; stopping
   altogether is Disconnect, which already exists. The line also says that
   lowering a rung keeps what is already in Odoo -- a sync is not undone by
   a setting, and a screen that implies otherwise is worse than no screen.

## What this does not build

- **No mirroring of Microsoft or Google mailbox permissions into Odoo.** It
  reads as the obvious move for shared mailboxes and it is two sources of truth
  for one permission, kept in sync forever. A shared mailbox is shared on
  purpose: its mail lands on records and Odoo's record ACL governs it. That is
  already the rule and it is enough.
- **No per-message visibility.** See above.
- **No company policy that sets a rung for everybody.** An administrator who
  can raise every colleague's rung from one page is the thing this document
  removes, with a nicer name.
- **No second consent for mail already synced.** Retroactive consent is a
  question nobody can answer honestly; the screen says what stays instead.

## Open

- Whether an administrator may *read* the rung of every personal mailbox.
  Assumed yes: they support it, they size the sync, and it is one selection
  value, not content.
- Where the connect screen lives -- an Odoo page after the callback, or a step
  in the Pantalytics-side flow. The Odoo page is cheaper and it is where the
  mailbox is created, so that is the assumption.
