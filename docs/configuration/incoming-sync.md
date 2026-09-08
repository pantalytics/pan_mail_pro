# Incoming Email Sync

Mail Pro can automatically sync incoming emails to Odoo, creating CRM Leads or Helpdesk Tickets.

## Prerequisites

Before enabling incoming sync:

1. **A notification mailbox exists** - the tick box on the mailbox that sends the system email; incoming sync waits for it
2. **The mailbox has working credentials** - on Microsoft 365 the owner's own sign-in; on Gmail and IMAP/SMTP the address's own account

## Enabling Sync

1. Go to **Settings → Technical → Email → Mail Pro → Mailboxes** (the arrow on step 3 of the checklist)
2. Open a mailbox
3. Replies to email sent from Odoo always land on the record they answer, with
   no setting at all. The two switches are about the rest:
   - **Receiving → Sync Other Email** - email that starts a new conversation.
     When it is on, a second question appears: only from existing contacts, or
     from anyone (which turns every sender into a contact).
   - **Sending → Sync Sent Items** - read the Sent Items folder of the user's own
     mail app back into Odoo. Only replies to emails Odoo already has; mail that
     starts a new conversation stays out.
4. On Microsoft 365, set the **Owner** whose sign-in reads the mailbox
5. Configure routing (see below)
6. Save

## Routing Configuration

By default, mail that starts a new conversation lands on the sender's contact
chatter. Tick **To Team** on the mailbox and pick a **Route to Team** alias to
have it create a record instead:

| Alias belongs to | What is created |
|------------------|-----------------|
| CRM team | A lead or opportunity |
| Helpdesk team (Enterprise) | A ticket |
| Any model with an alias | That model's record, through Odoo's own alias |

The alias is configured on the team itself, in that app's settings. Mail Pro
only points the mailbox at it. When no alias is set, mail falls back to the
contact's chatter, and the Mail Routing log flags that row for review.

## Sync Behavior

### What Gets Synced

- **Inbox:** Incoming emails
- **Sent Items:** replies to conversations Odoo already has, so mail
  written in your own mail app lands on the record it continues

### Filtering

- **Internal domains:** Required, set once in Settings → Mail Pro. Odoo
  suggests them from your mailboxes and company email; nothing syncs until the
  list has at least one entry. Mail where *every* party is one of your own
  domains is never synced. A mail with any outside recipient is
  correspondence and is still logged
- **Block list:** Per-contact exclusion

### Timing

- Sync runs automatically every **1 minute**
- Set **Sync Start Date** to import historical emails
- Default: sync from module activation date

## Block List

To exclude specific contacts from sync:

1. Open the contact's form
2. Go to **Email Sync** tab
3. Enable **Block Email Sync**

Emails from blocked contacts are skipped across all mailboxes.

## Monitoring

Check sync status:

1. **Mailbox health status** - Green/Yellow/Red indicator
2. **Logs** - Search for `[Incoming Mail]` in system logs
3. **Scheduled Actions** - Check "Mail Pro: Fetch Incoming Mail"
