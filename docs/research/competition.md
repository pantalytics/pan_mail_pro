# Who else puts an inbox in Odoo

Odoo Apps Store and Odoo's own documentation, read 16 September 2026. The
question: is [the conversation inbox](../plans/conversation-view.md) a category
nobody serves, or a crowded one?

Crowded, and two of them are free.

## In the Odoo Apps Store, 19.0

| Module | Vendor | Price | What it is |
|---|---|---|---|
| [Mail Client](https://apps.odoo.com/apps/modules/category/Client/browse?series=19.0) | Albirru Solutions | **Free**, open source | Three-pane inbox, two-way IMAP sync, shared team mailboxes, built for mailcow and Dovecot, also Gmail and Microsoft 365 |
| [OW Mail](https://apps.odoo.com/apps/modules/category/Mail/browse?series=19.0) | Openworx | **Free** | Backend mail client, IMAP/SMTP, multi-account, tags |
| [MailDesk Basic](https://apps.odoo.com/apps/modules/18.0/maildesk_mail_client) | Metzler IT GmbH | $449 | Unified inbox over Gmail, Outlook and IMAP with OAuth2, "Outlook-style conversation view", shared inboxes with permissions, HTML composer |
| [Advance Team Mailbox](https://apps.odoo.com/apps/modules/19.0/wbl_team_mailbox) | Weblytic Labs | $98 | Shared inbox with assignment, workflow states, SLA policies, canned replies, automation |
| Shared Inbox & Email CRM | Ranvals Software | $85 | Gmail, Outlook and IMAP shared inbox with CRM integration |
| Microsoft Graph Mail | Nexevolve | $99 | Send and receive Microsoft 365 mail over Graph, no SMTP or IMAP |

Two shapes, not one. **The mail client in Odoo** (Albirru, OW Mail, MailDesk)
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

## What this does to our claim

Three corrections we should make to ourselves rather than discover in a sales
call:

1. **"An inbox in Odoo" differentiates nothing.** There is a free one for 19.0
   with the same three-pane shape and the same two-way sync.
2. **The record beside the thread is not unique either.** MailDesk already
   advertises chatter sync with document links and replying with record
   context. What we can still claim is how: a rollup that stores no facts,
   against their separate mail store.
3. **The transport is where we are actually ahead.** Graph and the Gmail API
   rather than IMAP, the matcher, the routing log, the internal-domain gate.
   That is also exactly what the survey's largest cluster asked for, and what a
   free IMAP client cannot do: send as the person from a shared address, prove
   where a mail landed, refuse to sync what it should not.

MailDesk's own page names the seam everyone else punts on: the basic version
"does not synchronize flag changes, folder moves, or deletions back to mail
servers". That is the read-state problem, and it is why our answer is Odoo's
`mail.notification` rather than a state of our own.

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

Nothing about how many people run any of these. The store shows no download
count on these listings, there are no public reviews to weigh, and none of it
says whether a free three-pane inbox is any good once a real mailbox is behind
it. Before pricing anything against this list, install the Albirru module on the
dev instance and use it for a week.
