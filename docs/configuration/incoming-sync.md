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
   no setting at all. On the **Sync Settings** tab, pick a **Sync level** for
   the rest. Each level keeps strictly more than the one above it:
   - **Replies, in Odoo only** (default)
   - **Replies, in Odoo and your mail app** - your own replies are read back
     from the Sent folder and land on the same record
   - **Replies and new email, existing contacts only** - new conversations
     started by people who are already contacts
   - **Replies and new email, everyone** - new conversations from strangers
     too, who become contacts. Mail you start from your own mail app never
     enters, whatever the level: only your replies do.
4. On Microsoft 365, set the **Owner** whose sign-in reads the mailbox
5. Configure routing (see below)
6. Save

## Routing Configuration

### Team Selection

Select a **Team** (alias) to route emails to:

- **CRM Team** → Creates Leads
- **Helpdesk Team** → Creates Tickets
- **Sales Team** → Creates Opportunities

### Contact Type Routing (from anyone)

When **Sync Other Email** accepts mail from anyone, configure routing per contact type:

| Contact Type | Example | Routing |
|--------------|---------|---------|
| Known Partner | Existing customer | Follow partner settings |
| Unknown External | New inquiry | Create Lead |
| Internal | Employee | Skip (not synced) |

## Sync Behavior

### What Gets Synced

- **Inbox:** Incoming emails
- **Sent Items:** Outgoing emails (for threading)

### Filtering

- **Internal domains:** Required, set once in Settings → Mail Pro. Odoo
  suggests them from your mailboxes and company email; nothing syncs until the
  list has at least one entry
- **Internal users:** Employees with Odoo accounts are excluded
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
