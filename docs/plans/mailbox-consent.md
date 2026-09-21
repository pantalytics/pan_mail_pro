# Consent: who decides what a mailbox shares

Status: **design only**, nothing below is built. Decisions taken 2026-09-21.

## Azure is not the boundary; Odoo is

Mail Pro asks Microsoft for delegated scopes -- `Mail.ReadWrite(.Shared)` and
`Mail.Send(.Shared)`. They read wide, and they are the right ask anyway: a
delegated token acts *as the person* and can never reach a mailbox that person
cannot already open in Outlook. It grants no tenant-wide access and no view of
anybody else's mail.

Splitting the app registration in two or three, one per scope combination, buys
nothing and costs every customer an Azure round trip they can get wrong. The
question that matters is not what the token may read. It is **what we copy into
Odoo, where colleagues see it**, and that question is answered entirely on our
side of the line, by `sync_level` (ARCHITECTURE.md §3). One Azure app, the
narrowing in the product.

So this document is about the three mailbox types and who holds the switch on
each.

## The three types

| Type | Whose mail | Who decides what Odoo reads |
|---|---|---|
| Notification | nobody's -- system mail Odoo itself sends | administrator; there is nothing to consent to |
| Shared | the company's, by construction | administrator, without asking anyone |
| Personal | one person's | **the owner, and nobody else** |

**Shared mailboxes need no consent layer.** A shared mailbox is shared on
purpose: the admin sets its level, its mail lands on records, and Odoo's record
ACL governs who reads it. Microsoft's own permissions decide who can technically
connect it and nothing more. Mirroring those permissions into Odoo ACLs is two
sources of truth for one right, kept in sync forever; it is not built.

**The notification mailbox** sends and is never read for anyone's
correspondence. It stays the administrator's.

## Personal mailboxes: four decisions

**1. Only the owner raises the rung.** A field-level guard in
`pan.mail.mailbox.write()` -- a record rule cannot express "this field" --
refuses any raise of `sync_level` on a personal mailbox by anyone but
`owner_user_id`. An administrator may still lower it, archive the mailbox and
disconnect the account: stopping a sync is never blocked, starting one is.
There is no policy setting that raises everybody's rung at once; that is the
thing this removes, with a nicer name.

**2. Ask once, at connect, on its own screen.** The OAuth callback comes back
to one page with the four rungs, `replies` preselected, one line each saying who
ends up seeing the mail. Closing the page leaves `replies`. It is the only place
in the product that says *mail that lands on a record is visible to everyone who
can open that record*, because it is the only moment where that sentence can
still change a decision. The screen records who agreed and when.

**3. What is in Odoo stays in Odoo.** Lowering a rung stops the supply; it does
not remove what is already on a record, and neither does disconnecting. Mail on
a record has become part of a file colleagues work from. The consent screen and
the owner's own line say so in those words, before the choice, not after it.
Odoo has no per-message visibility and we are not building one.

**4. The owner can always find the switch.** In Preferences and at the top of
their mailbox in the Inbox: what Odoo reads right now, in the rung's own words,
with Change and Stop beside it. Stop drops to `replies`; stopping altogether is
Disconnect, which exists.

## The administrator's overview

One list, reachable from the Mail Pro settings: **who, which level, since
when**. Per internal user: connected or not, the mailbox address, the rung, and
the date the owner agreed to it. Nothing else -- no folder names, no message
counts, no last-sync timestamps, no content. It answers "is this rolled out and
who agreed to what", which is what an administrator needs, and it is not an
activity meter on a colleague.

## Open

- Where the consent screen lives: an Odoo page after the OAuth callback, or a
  step on the Pantalytics side. The Odoo page is cheaper and it is where the
  mailbox is created, so that is the assumption.
- Whether the date of agreement is worth a field of its own or rides on the
  mailbox's write metadata. A field, probably: the overview's column is only
  honest if lowering the rung does not rewrite it.
