# The market for email in Odoo

Odoo Apps Store, the vendors' own pricing pages and Odoo's own documentation,
read 16 and 17 September 2026. The question behind it: is
[the conversation inbox](../plans/conversation-view.md) a category nobody
serves, what do these buyers already pay for, and where is the money.

Everything below is either measured (a figure on a page, quoted), claimed (a
vendor says so on a listing and nobody checked), or reasoned (mine, marked as
such). They are not the same thing and the difference matters more here than
the conclusions.

## The two problems

The store treats email in Odoo as one product. It is two.

1. **Transport.** Send and receive as the real mailbox on Microsoft 365, Gmail
   or IMAP, and have the mail land on the right Odoo record without the
   catchall and alias contraption.
2. **The mailbox.** A screen you can read and answer mail in.

A module can do either without the other, and almost all of them do.

## Inside the Odoo Apps Store, 19.0

| Module | Vendor | Price | Downloads | What it is |
|---|---|---|---|---|
| [Mail Client](https://apps.odoo.com/apps/modules/19.0/mail_client) | Albirru Solutions | **Free**, open source | 66 | Three-pane inbox, two-way IMAP sync, shared team mailboxes, built for mailcow and Dovecot, also Gmail and Microsoft 365 |
| [OW Mail](https://apps.odoo.com/apps/modules/19.0/ow_mail) | Openworx | **Free** | 137 | Backend mail client, IMAP/SMTP, multi-account, tags. OAuth through a beta companion module |
| [MailDesk Basic](https://apps.odoo.com/apps/modules/18.0/maildesk_mail_client) | Metzler IT GmbH | $449 | not shown | Unified inbox over Gmail, Outlook and IMAP with OAuth2, "Outlook-style conversation view", shared inboxes with permissions |
| [Advance Team Mailbox](https://apps.odoo.com/apps/modules/19.0/wbl_team_mailbox) | Weblytic Labs | $98 | not shown | Shared inbox with assignment, workflow states, SLA policies, canned replies |
| [Shared Inbox & Email CRM](https://apps.odoo.com/apps/modules/19.0/mail_center_odoo17) | Ranvals Software | $85 | 1 | Gmail, Outlook and IMAP shared inbox with CRM integration |
| [Microsoft Graph Mail](https://apps.odoo.com/apps/modules/category/Mail/browse?series=19.0) | Nexevolve | $99 | not shown | Send and receive Microsoft 365 mail over Graph, no SMTP or IMAP |

Two shapes. **The mail client in Odoo** (Albirru, OW Mail, MailDesk)
reproduces Outlook inside the backend. **The team inbox** (Weblytic, Ranvals)
reproduces a helpdesk: assignment, statuses, SLA. Our design is the first shape
and deliberately refuses the second.

## And from Odoo itself

The [Mail Plugin](https://www.odoo.com/documentation/19.0/applications/general/integrations/mail_plugins.html)
for Outlook and Gmail is free, official, and it is the opposite bet: you stay in
your mail client and a side panel logs the message to the chatter or opens a
lead. No install inside Odoo, no migration of habits. Eight of the 28 survey
respondents said they work outside Odoo, and this is what that half is offered
today.

**The platform risk, stated plainly.** Odoo could ship this. `message_post` in
19.0 already accepts `outgoing_email_to` next to `partner_ids`, described in
its own docstring as experimental, which is the first sign of Odoo separating
the addressee from the follower. If Odoo ever ships a real mailbox screen, the
whole store category goes to zero in one release. Nothing suggests it is
imminent, and no amount of reading the source tells you a roadmap. It is a risk
to carry, not one to plan around.

## Who solves which of the two problems

There are two problems, not one, and almost nobody does both.

1. **Transport.** Send and receive as the real mailbox on Microsoft 365, Gmail
   or IMAP, and have the mail land on the right Odoo record without the
   catchall and alias contraption.
2. **The mailbox.** A screen you can read mail in.

| | Sends and receives as the mailbox itself | Lands on the right record automatically, without aliases | Inbox screen |
|---|---|---|---|
| **Odoo out of the box** (`microsoft_outlook`, `google_gmail`) | Partly. OAuth on the outgoing server, but incoming runs on catchall and aliases | Yes, through the aliases, which is the part people are complaining about | No. Discuss is a notification queue |
| **Odoo Mail Plugin** (Outlook/Gmail side panel, free) | No, you are in Outlook | Manual, you press a button per mail | No, by design |
| **Mail Client**, Albirru, free, 66 downloads | Per mailbox, over Odoo's own outgoing servers plus IMAP or OAuth | **No, refused on purpose**: "No Chatter bridge. Logging mail against Odoo records stays Odoo's own job, deliberately" | Yes, three panes |
| **OW Mail**, Openworx, free, 137 downloads | IMAP/SMTP, with OAuth for Gmail and M365 through a beta companion module | Half. You create the record from a mail by hand; after that, replies post to its chatter automatically | Yes |
| **MailDesk**, Metzler IT, $449 | OAuth for Gmail and Outlook, plus IMAP | Claims chatter sync with document links. Not verified beyond the listing | Yes, "Outlook-style conversation view" |
| **Advance Team Mailbox**, Weblytic, $98 | Claims Gmail, Outlook, Zoho, M365, IMAP | Not stated on the page | Yes, plus assignment, statuses and SLA |
| **Shared Inbox & Email CRM**, Ranvals, $85, 1 download | Claims Gmail, Outlook, IMAP | Claimed, not verified | Yes |
| **Microsoft Graph Mail**, Nexevolve, $99 | Yes, Graph without SMTP or IMAP | Through Odoo's normal path, so still the aliases | No |
| **Mail Pro today** | Yes, per mailbox over Graph, the Gmail API or IMAP | Yes, the matcher and the routing log, no aliases | **No** |

Read down the middle column. It is the one nobody else really does: the free
one refuses it in its release notes, OW Mail does half of it, and the two that
claim it are claims on a listing page. Read the last column and it is the only
one we do not have.

So the two products in this market are not competing for the same thing. The
mail clients are mail clients that happen to run inside Odoo; what makes mail
worth having inside an ERP is the middle column, and that is where we already
are.

## What these buyers already pay for, outside Odoo

This is the part the store listings hide. Three of the 28 respondents named a
tool they already bought, and none of them was an Odoo module: Missive twice,
Chatwoot once, Brevo once as a sending workaround.

| Tool | Price (per user or agent, per month, billed annually) |
|---|---|
| [Missive](https://missiveapp.com) | $14 Starter (max 5 users), $24 Productive, $36 Business |
| [Front](https://front.com) | $25 Starter (max 10 seats), $65 Professional, $105 Enterprise |
| [Chatwoot](https://www.chatwoot.com/pricing) | $0 Hacker, $19 Startups, $39 Business, $99 Enterprise. Self-hosted community edition is free |

Read that against the store table. A five-person sales team on Missive
Productive is $120 a month, about $1,440 a year, every year. The most expensive
module in the Odoo store is $449 once. **The people with this problem are
already paying roughly three times the price of the most expensive Odoo module
every single year, to a company that knows nothing about their quotes and
tickets.**

That is the number that matters, and it says the Odoo store is not where the
money is. It is where the free-and-cheap end of the category lives, on a
platform that takes 30% of a sale and prices per version.

## Where the money would come from (reasoning, not research)

Three ways to be paid for this, with what each implies:

1. **A paid module in the store.** One-off, $85 to $449, Odoo keeps 30%, and
   you re-sell it at every version. The observed download counts on this
   category are 1, 66 and 137. Even generously, this is a few thousand euro a
   year and a support burden. It is a distribution channel, not a business.
2. **Per-seat subscription, priced against Missive rather than against the
   store.** Mail Pro already has the machinery: a licence, a signed
   entitlement, a daily heartbeat. At a fraction of Missive's $24 it is still an
   order of magnitude more per customer than any module in that table, and the
   pitch is the one thing Missive structurally cannot do: the quote and the
   ticket next to the thread.
3. **Consulting pulled through by the product.** Two of the 28 already paid
   developers for their own CC and follower fixes; three are partners who would
   resell. This is the shortest path to revenue and the one that does not scale.

The honest reading of the survey is that (2) is the bet and (1) is the shop
window.

## Sizing, with the assumptions showing

Odoo's own [About Us](https://www.odoo.com/page/about-us) page claims **28
million users**, 21,000+ partners and 8,000+ employees. Secondary sources put
the customer count around 170,000 and 2025 revenue near €650M; those are blog
aggregations, not Odoo's own figures, and should be treated as such.

Everything after that is multiplication with numbers nobody has measured: what
share is on 19.0, what share runs Microsoft 365 or Google Workspace, what share
has a team that lives in mail rather than one admin who sends invoices. Each
guess moves the answer by a factor, so the product of four guesses is not an
estimate, it is a decoration. What can be said without inventing anything:

- The category is large enough that six vendors keep building for it and Odoo
  itself ships two integrations for it.
- The paying end of it is visibly not in the Odoo store, because the store's
  own numbers are 1, 66 and 137 downloads.
- Our own list is the only population we can actually measure, and 18 of 28 of
  them already send from inside Odoo.

Before anyone models revenue, ask the list what they pay today for email tools.
That one question converts every guess above into an observation.

## What we would have to believe

Written as bets, so they can be checked rather than argued:

1. **That the record beside the thread is worth switching for.** If it is not,
   the inbox is a worse Outlook and the free module is the ceiling.
2. **That people will pay per seat for something inside Odoo.** They already do
   for Missive; nobody has shown they will inside an ERP.
3. **That Odoo does not ship a mailbox screen in the next two releases.**
4. **That the transport half stays hard.** It is hard today because Graph,
   Gmail and IMAP each behave differently and the catchall is a trap. If Odoo
   smooths that out, the wedge narrows.

Bet 1 is the cheapest to test and nobody has tested it.

## So why does nobody seem to use them?

Worth asking, because the answer is a warning about the design we just agreed.
In 28 replies **nobody named an Odoo email module at all**. What they did name:
Brevo to get mail out, Missive for shared inboxes, Chatwoot alongside Odoo, and
two people who paid developers to write their own CC and follower fixes. So it
is not that this problem has no budget. The budget goes somewhere else.

Four readings, and the evidence for each:

1. **Nobody goes looking.** The problem arrives as a symptom, "the customer did
   not get the thread", not as a product category. You search a store for a
   name you already have, and these people had no name for it.
2. **When they do buy, they buy a category, not a module.** Missive, Chatwoot
   and Front are known things with known prices. "An Odoo inbox module" is not
   a thing anyone is shopping for.
3. **Partners build rather than buy.** Three of the 28 are Odoo partners, and
   the two who solved a mail problem wrote the code themselves. A module that
   touches all of a customer's mail is a dependency you carry across every
   version upgrade.
4. **Each annoyance is below the threshold.** No CC, followers, formatting:
   three minutes of irritation each, never a budget line. Routing around it by
   opening Outlook is cheaper than a purchase decision, and eight of the 28 do
   exactly that.

**And the category is a graveyard.** The store carries the same idea for Odoo
10, 11, 13, 17, 18 and 19, from different vendors each time. The free 19.0 one
shows 66 downloads. People keep building an inbox inside Odoo and it keeps not
sticking, which is the pattern to take seriously: an inbox has to be as good as
Outlook *every day*, and the day it is not, the user has Outlook already open.

**What this does to the plan.** It does not kill the design, it prices it. The
fourth pane, the record with its chatter, is the only thing in it that Outlook
can never do, so it had better be the reason to open the screen rather than a
detail on the right. Everything else in the inbox is a daily comparison with a
mail client we will lose. The wedge the survey actually paid for is transport:
setup that finishes, the right sender, mail that arrives, and the routing log
that proves where it went.

**The caveat that matters.** Nobody was asked. "Have you tried an email module
from the Odoo store, and why did you stop" was not in the survey, and absence
from 28 self-selected replies is weak evidence. One follow-up question to the
18 who already send from Odoo settles this better than any amount of reasoning
here, and they have already shown they reply.

## What this does not tell us

Three limits worth keeping in front of the conclusions.

**The store numbers are thin.** Downloads are shown for the free modules (1, 66,
137) and not for the paid ones, there are no public reviews to weigh, and a
download is not an install and an install is not a user.

**Nobody has used any of them.** Every line about what a paid module does comes
from its own listing. Two claims in particular are unverified and load-bearing:
MailDesk's chatter sync with document links, and Weblytic's routing. Installing
the two free ones on the dev instance with a real mailbox behind them costs a
week and would replace half of this page with facts.

**The survey population is ours, not the market's.** 28 self-selected replies
from people who already use a Pantalytics tool. They are more Odoo-committed and
more technical than the average Odoo customer, and nobody asked them about
price.
