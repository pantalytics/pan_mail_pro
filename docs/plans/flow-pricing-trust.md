# The flow, the price and who can read the mail

Status: proposal, 2026-09-29. Nothing here is decided yet. It is meant for one
conversation with Daniel about three things: where a customer meets the price,
how we charge, and what we have to tell a customer about who can read their
mail once it is in Odoo. I checked the facts against the code and the two plan
documents on that date. Once something is decided, the price part goes to
[mail-pro-paid.md](https://github.com/pantalytics/mail-pro-admin/blob/main/docs/plans/mail-pro-paid.md)
and the security part to ARCHITECTURE.md §10. Then this file can go.

## Short version

1. Keep charging per Odoo database, on the number of mails sent per day. On
   the pricing page, describe each plan by who it is for (one person, a team,
   the whole company). Do not count seats.
2. The price shows up in three places, all on the administrator's side: the
   public pricing page, the licence line under Settings, and the message a
   mail gets when it is held until tomorrow. Never on a normal user's screen.
3. Only the owner of a personal mailbox may change how much of it Odoo reads.
   And the product has to say, before the consent screen and next to the
   setting, who in Odoo will be able to read what comes in. Right now it does
   not say that anywhere.

## 1. How people move through the product today

The administrator and the user never see the same screen.

```
Administrator                                     User
─────────────                                     ────
app.mailpro.pantalytics.com/start
  └ install module
  └ Connect to Pantalytics (pairing code)   <── entitlement: plan, daily_send_limit
  └ Setup checklist
      1 provider   (Azure app registration, see #253)
      2 internal domains
      3 notification mailbox
  └ Users: Send Mail Pro Invite ─────────────────> banner: "Your mailbox is not connected yet"
  └ Settings: licence line, Usage and billing        My Preferences → Mail Pro
                                                       Connect Mailbox → provider consent screen
                                                       Send from, Sync level, Send Test Email
Mailbox form (Sync Settings tab, all mailboxes)      Inbox (mailbox managers only)
```

Today the price is visible in two places: the licence line under Settings, and
a "Usage and billing" link that opens the wrong page
([#245](https://github.com/pantalytics/pan_mail_pro/issues/245)). When the
trial ends the administrator sees a banner, the same kind the setup steps use.

## 2. Price

### The two options

| | Send limit per database (decided 2026-09-16, built) | Per person (Daniel's preference) |
|---|---|---|
| What we count | mails per day, one number per database | connected accounts; the notification mailbox counts as its owner |
| Buyers know this from | Brevo | Missive, Front, Odoo itself |
| A 40-person customer pays | EUR 39 or 100 | 40 times something |
| What happens at the limit | a mail waits until tomorrow | the sixth colleague presses Connect and is refused |
| Work for us | none | seats bought vs used, pro-rating, invoices nobody expected |
| Already built | yes: `daily_send_limit` is in the signed entitlement and the module reads it. The hold-until-tomorrow part is [#128](https://github.com/pantalytics/pan_mail_pro/issues/128) | no |

### What I would do

Keep the send limit. The row that decides it for me is "what happens at the
limit". With seats, the limit hits a new colleague. The administrator invited
them five minutes ago, they press Connect, and we say no. Or we say yes and
send the administrator an invoice they did not ask for. Both are bad. A mail
that waits until tomorrow is not great either, but nobody is locked out, and
the module already does this while setup is unfinished.

Daniel has a point about the words. People think in people, not in mails per
day. So the pricing page talks about people, and the mail count does the work
underneath:

| Plan | For | Mails a day | Price |
|---|---|---|---|
| Free | one person | 100 | EUR 0 |
| Pro | a team | 1,000 | EUR 39 / month |
| Max | the whole company | 5,000 | EUR 100 / month |

"One person" and "a team" are a description, not a rule we enforce. The 100
was picked because it is enough for one office worker and runs out when a
second person joins (see mail-pro-paid.md). So at the bottom the two models
agree anyway.

What we give up: a 40-person company on Pro pays us about a tenth of what
Missive would charge them. Max catches some of that, not all. I think we accept
this until we have a customer that size. At that point we change a price, not
the model.

If we ever do count seats, do it on our server, not in the module.
[#253](https://github.com/pantalytics/pan_mail_pro/issues/253) routes every
OAuth consent through app.mailpro.pantalytics.com. From then on we see each
connected account the moment it is created, per workspace, without having to
match it against a heartbeat.

One loose end: [#176](https://github.com/pantalytics/pan_mail_pro/issues/176)
already notes that the product brief and mvp.md still say "per seat". This
file does not fix that, it only repeats the decision with the reasoning.

### Where the customer meets the price

| Where | What it says |
|---|---|
| app.mailpro.pantalytics.com/pricing, linked from /start and the install doc | the table above |
| The licence line under Settings: "Pro, 412 of 1,000 today", with one link to /billing (#245) | how much of the plan is used |
| A held mail: the `failure_reason` on the mail, and a banner for managers | why it waits, and where to change the plan |

Nothing on My Preferences and nothing in the Inbox. A normal user did not buy
the product and cannot upgrade it. A price on their screen only produces a
question to a colleague, or a ticket to us.

## 3. Who can read the mail

### Four questions, and the consent screen answers one

| | Question | Answer today | Where a customer can see this |
|---|---|---|---|
| 1 | What may Mail Pro do in the mailbox? | Read and write mail, send mail. Always per user, never as the whole tenant. (`Mail.ReadWrite` + `Mail.Send`; `gmail.modify` + `gmail.send`) | The Microsoft or Google consent screen. The customer's Azure admin sees the app |
| 2 | What does the module actually do with that? | Reads replies to Odoo's own mail, always. Reads more only if the sync level says so. Sends. Puts labels on Gmail. Never deletes or moves anything. On SMTP it stores its own copy in Sent | docs/security.md, and the sync level table on the form |
| 3 | Once a mail is in Odoo, who can read it? | Anyone who can open the record it landed on. If there is no record, it lands on the sender's contact card, and every internal user can read contact cards | Nowhere |
| 4 | Who can widen 2 and 3, and for whom? | The owner, on My Preferences. But also any mailbox manager, on the mailbox form, for anyone's personal mailbox | Nowhere |

The customer's IT department looks at question 1. That answer is wrong in two
directions. "Read and write access to your mail" sounds like the app could
empty your inbox, which it never does. And it says nothing about colleagues
reading the copy, which can happen. That is the problem Rutger named: copying
mail into Odoo can go further than what the user agreed to in Azure, and Azure
cannot warn about it because Azure does not know what Odoo does with the copy.

### How the widening happens, step by step

Someone connects their mailbox and picks "Replies only". A mailbox manager
opens Settings → Mail Pro → Mailboxes, opens that person's personal mailbox and
sets the sync level to "Replies and new email, everyone". From the next sync
run, every new conversation in that inbox is imported with the owner's own
token. Whatever the matcher cannot place lands on the sender's contact card,
which the whole company can read. The manager reads all of it in the Inbox.
The owner is not told. Microsoft sees a token it issued being used within the
scopes it granted. Nothing errors, nothing is logged.

It is less bad than it sounds in two ways. The live read of a mailbox ("All
email") already refuses everyone except the owner, so the code knows this
rule. And mailbox managers are the people who set up mail, a small group. It
is worse in one way: on Microsoft, a shared mailbox is also read with a
member's personal token. So the same write by a manager reaches a colleague's
consent twice.

### What I would change

1. Only the owner can change the sync level of a personal mailbox. A manager
   sees it read-only on the mailbox form, with one line saying whose decision
   it is. One check in `pan.mail.mailbox.write`, next to the
   `_check_mailbox_is_mine` the user side already has. A manager who wants it
   lower for compliance reasons can disconnect the mailbox, which they can
   already do. I would not build a separate "lower only" rule for a case
   nobody has asked for. Filed as
   [#258](https://github.com/pantalytics/pan_mail_pro/issues/258), separate
   from the rest of this file.
2. Add a column to the sync level table: who in Odoo sees it. The table on the
   mailbox form and on My Preferences now says "Synced" or "Not synced" per
   situation. It should also say where the mail lands and who can read it.
   For replies: "on the record it answers, for the people who can open that
   record". For new mail without a record: "on the sender's contact card, for
   every colleague". This is text, not code.
3. One screen of ours before Microsoft's or Google's. Right now Connect
   Mailbox jumps straight to the provider. Put one Odoo screen in between with
   three lines: what Odoo reads (replies to its own mail, more only if you
   choose that), where it lands (on the record, otherwise on the contact
   card), who sees it (the people who can open that record). Then the button.
4. Add questions 3 and 4 to docs/security.md. That page covers the consent
   scopes and the encryption, which is the provider half. An IT department
   reads it to decide. It should answer "who in Odoo can read this" in one
   table.
5. #253 changes question 1, and the page has to be written for that too. With
   one Pantalytics-owned app, the customer consents to our app and the refresh
   token belongs to it. Either the token exchange happens on our server and we
   keep nothing afterwards, or the token lives only in their database and our
   server never touches mail. Whichever way #253 goes, the page has to say it
   in one sentence. From that day on, "who can read my mail" includes
   Pantalytics until we show it does not.

### What is fine as it is

- The Inbox reads as the logged-in user, no sudo. An imported message has the
  access rights of its record. That is why question 3 needs a sentence and not
  a redesign.
- The live read is guarded by ownership. `_own_mailbox` refuses a manager.
  Change 1 copies that rule.
- Nothing is ever deleted at the provider. True on all three. On Google the
  scope is wider than what we use, because Google has no smaller scope that
  allows labels. Worth one line on the page.
- Internal mail is filtered, and sync refuses to run until the domain list
  exists. That is the line between customer mail and colleagues' mail, and it
  holds.

## Order

1. #258, the write check. Small, and it closes the widening on its own.
2. The extra column and the screen before the consent screen. One PR, text
   and one view.
3. docs/security.md questions 3 and 4, and the pricing page in people-words.
4. #245, the billing link.

The rest of this file is something to talk about, not something to build.
