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

## What this does not tell us

Nothing about how many people run any of these. The store shows no download
count on these listings, there are no public reviews to weigh, and none of it
says whether a free three-pane inbox is any good once a real mailbox is behind
it. Before pricing anything against this list, install the Albirru module on the
dev instance and use it for a week.
