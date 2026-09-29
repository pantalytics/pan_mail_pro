# The flow, the price and the trust line

Status: **proposal, 2026-09-29, nothing decided.** Written to settle three
things with Daniel in one sitting: where pricing appears in the product, how
Mail Pro is priced, and what a customer has to be told about who can read
their mail once it is in Odoo. Facts checked against the code and the two
plan documents on that date. What is decided moves to
[mail-pro-paid.md](https://github.com/pantalytics/mail-pro-admin/blob/main/docs/plans/mail-pro-paid.md)
(price) and [ARCHITECTURE.md §10](../../ARCHITECTURE.md) (trust); this file
then goes.

## The three calls, up front

1. **Keep the daily send limit per Odoo instance. Sell it in people.** The
   meter stays what is enforced; the pricing page says who each tier is for
   (one person, a team, the company). No seat count, no seat cap.
2. **Pricing has three doors, all for the administrator.** The public page,
   the licence line under Settings, and the moment a mail is deferred. It
   never appears on a user's own screens.
3. **The sync level of a personal mailbox belongs to its owner alone**, and
   the product says out loud, before the consent screen and on the ladder,
   who in Odoo will read what it imports. That sentence is what makes the
   Azure grant and the Odoo reality agree.

## 1. The flow as it is

Two people walk through the product, and they never meet on a screen.

```
Administrator                                     User
─────────────                                     ────
app.mailpro.pantalytics.com/start
  └ install module
  └ Connect to Pantalytics (pairing code)   <── entitlement: plan, daily_send_limit
  └ Setup checklist
      1 provider   (Azure app registration: the wall, #253)
      2 internal domains
      3 notification mailbox
  └ Users: Send Mail Pro Invite ─────────────────> banner: "Your mailbox is not connected yet"
  └ Settings: licence line, Usage and billing        My Preferences → Mail Pro
                                                       Connect Mailbox → provider consent screen
                                                       Send from, Sync level, Send Test Email
Mailbox form (Sync Settings tab, all mailboxes)      Inbox (mailbox managers only)
```

Where pricing touches it today: the licence line under Settings and one
"Usage and billing" link that lands on the wrong page
([#245](https://github.com/pantalytics/pan_mail_pro/issues/245)). The trial
ends on unlicensed, and the module tells the administrator so in the same
banner machinery the setup steps use.

## 2. Price

### The two models on the table

| | Daily send limit per instance (decided 2026-09-16, built) | Per person (Daniel) |
|---|---|---|
| Unit | one number per database | a connected account; the notification mailbox counts its owner |
| What buyers already know | Brevo | Missive, Front, Odoo itself |
| Revenue on a 40-person customer | EUR 39 or 100 | 40 × something |
| Where it bites | a mail goes tomorrow, nobody is refused | the sixth colleague presses Connect on a five-seat plan |
| What we run by hand | nothing | seats bought vs used, proration, the surprise invoice |
| Built today | `daily_send_limit` is in the signed entitlement and the module reads it; the throttle is [#128](https://github.com/pantalytics/pan_mail_pro/issues/128) | nothing |

### The call

Keep the meter. The deciding line is the fifth row. A seat cap bites at the
one moment that has to work: a new colleague, invited by the administrator
five minutes ago, presses Connect and is refused. Either we refuse (the
product reads as broken to the newest user) or we bill (the administrator gets
an invoice they did not click). A deferred mail bites nobody in the face and
is the mechanism the module already has for the not-configured-yet window.

Daniel is right about the words, not the unit. Buyers think in people, so the
pricing page says so and the meter stays underneath:

| Plan | Who it is for | Mails a day | Price |
|---|---|---|---|
| Free | one person | 100 | EUR 0 |
| Pro | a team | 1,000 | EUR 39 / month |
| Max | the company | 5,000 | EUR 100 / month |

"One person" and "a team" are guidance, not enforcement. The 100 was chosen
because it carries one office worker and runs out when a second joins
(mail-pro-paid.md), so the two models already agree at the bottom.

**What we drop, named.** A 40-person customer on Pro pays a tenth of what
Missive would charge them. Max at EUR 100 catches some of that by volume, not
all of it. We take that until the first customer of that size exists; then
the question is a price, not a model.

**Where a seat would be counted if we ever do.** Not in the module.
[#253](https://github.com/pantalytics/pan_mail_pro/issues/253) puts every OAuth
consent through app.mailpro.pantalytics.com, so a connected account becomes
something we observe at the moment it is created, per workspace, with no
reconciliation against a heartbeat. If per person ever wins, it wins there.

**Housekeeping.** [#176](https://github.com/pantalytics/pan_mail_pro/issues/176)
already says the product brief and mvp.md contradict the decision; this file
adds nothing to that, it repeats the decision with the reason Daniel asked
for.

### Where pricing appears

Three doors, all on the administrator's path, none on the user's.

| Door | Where | Says |
|---|---|---|
| Public | app.mailpro.pantalytics.com/pricing, linked from /start and the install doc | the table above |
| Settings | the licence line: "Pro, 412 of 1,000 today", one link to /billing (#245) | how much of the plan is used |
| The deferral | the moment a mail goes tomorrow: `failure_reason` on the mail and the banner for managers | why it waits and where the plan is |

Nothing on My Preferences, nothing in the Inbox. The user did not buy it and
cannot; a price on their screen is a question for a colleague and a ticket
for us.

## 3. Trust: who can read the mail

### Four layers, and only the first is on the consent screen

| Layer | Question | Answer today | Where it is visible |
|---|---|---|---|
| 1 Provider grant | What may Mail Pro do in the mailbox? | Delegated, per user: read and write mail, send. `Mail.ReadWrite` + `Mail.Send`; `gmail.modify` + `gmail.send`. Never application permissions | Microsoft's or Google's consent screen; the customer's Azure admin |
| 2 What the module does with it | What does it actually read, write, delete? | Reads replies always, more per `sync_level`. Sends as. Labels on Gmail. Never deletes, never moves. SMTP files its own Sent copy | docs/security.md, the ladder on the form |
| 3 Who in Odoo reads the copy | Once imported, who sees it? | Whoever may read the record it landed on. **The fallback record is the sender's contact, and every internal user reads contacts** | Nowhere |
| 4 Who may change 2 and 3 | Who can widen what is read, for whom? | The owner on My Preferences, **and any mailbox manager on the mailbox form, for anybody's personal mailbox** | Nowhere |

Layer 1 is what the customer's IT department reviews, and it understates
layer 3 in one direction and overstates layer 2 in the other. "Read and write
access to your mail" sounds like a machine that may empty your inbox; the
truth is narrower (it never deletes) and wider (a colleague may end up reading
it). That is the gap Rutger named: copying mail into Odoo can reach further
than the Azure grant, and the grant cannot say so because Azure has no concept
of what Odoo does with the copy.

### The escalation, concretely

A person consents to Mail Pro reading their mailbox, and picks *Replies only*.
A mailbox manager opens Settings → Mail Pro → Mailboxes, opens that person's
personal mailbox and sets the sync level to *Replies and new email, everyone*.
From the next cron run, every new conversation in that inbox is imported with
the owner's token; anything the matcher cannot place lands on the sender's
contact, which the whole company reads, and the manager reads all of it in the
Inbox. The owner is not told. The provider sees a token it issued being used
within the scopes it granted. Nothing errors and nothing logs.

Two facts make this narrower than it sounds and one makes it worse. Narrower:
the live read of a mailbox (*All email*) already refuses everybody but the
owner, ownership rather than group, so the model knows this rule; and the
manager group is the group that runs the mail setup, which is a small circle.
Worse: on Microsoft the *shared* mailbox is read with a member's personal
token too, so the same manager write reaches a colleague's grant twice.

### The calls

1. **The sync level of a personal mailbox is writable by its owner only.** The
   manager sees it read-only on the mailbox form, with a line saying whose
   decision it is. One check in `pan.mail.mailbox.write`, next to the
   `_check_mailbox_is_mine` the user side already runs. Dropped case: a manager
   who needs it lowered for compliance disconnects the mailbox, which they
   can already do; a separate "lower only" rule is a second rule for a case
   nobody has had. Filed as
   [#258](https://github.com/pantalytics/pan_mail_pro/issues/258), independent
   of the rest of this file.
2. **The ladder gets a third column: who in Odoo sees it.** The consequence
   table on the mailbox form and on My Preferences says *Synced* / *Not
   synced* per situation. It should also say where it lands and who reads it:
   "on the record it answers, for the people who may open that record" for
   replies; "on the sender's contact, for every colleague" for new mail
   without a record. That is the sentence that turns a preference into an
   informed one, and it is copy, not code.
3. **One screen of ours before the provider's.** Connect Mailbox goes
   straight to Microsoft or Google. One Odoo screen in between, three lines:
   what Odoo will read (replies to its own mail, more only if you choose it),
   where it lands (on the record, otherwise on the contact), who sees it (the
   people who may open that record). Then the button. The provider's screen
   then says less than ours, which is the right way round.
4. **docs/security.md grows layers 3 and 4.** It documents the grant and the
   encryption, which is the provider half. The customer's IT department reads
   it to decide; it has to answer "who in Odoo can read this" in one table.
5. **#253 changes layer 1 and the page has to be written for that shape.**
   With one Pantalytics-owned app, the customer consents to *our* app and the
   refresh token is minted for it. Either the exchange happens on our server
   and we hold nothing afterwards, or the token lives only in their database
   and our server never sees mail. Whichever #253 decides, the trust page
   states it in one sentence, because from that day the honest answer to
   "who can read my mail" includes Pantalytics until proven otherwise.

### What is fine and stays

- **No sudo for an answer.** The Inbox and the conversation methods read as
  the user; an imported message carries the ACL of its record. Nothing to
  change, and it is the reason layer 3 is a sentence rather than a redesign.
- **Ownership guards the live read.** `_own_mailbox` refuses a manager. Keep
  that as the model for call 1.
- **Never delete at the provider.** True on all three; on Google the scope is
  wider than the use because Google has no narrower one that allows labels.
  Say so, in one line, on the trust page.
- **Internal mail is filtered, fail-closed.** The domain gate refuses sync
  until the list exists. That is the boundary between correspondence and
  colleagues' mail, and it holds.

## Order

1. #258, the write guard. Small, and it closes the escalation on its own.
2. The third column and the pre-consent screen: one PR, copy and one view.
3. docs/security.md layers 3 and 4, and the pricing page in people-words.
4. #245, the billing link.

Everything else here is a conversation, not a change.
