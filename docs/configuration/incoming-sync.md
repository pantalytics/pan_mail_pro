# Incoming Email Sync

Mail Pro can automatically sync incoming emails to Odoo, creating CRM Leads or Helpdesk Tickets.

## Prerequisites

Before enabling incoming sync:

1. **Create a Notification mailbox** - Required to handle emails from unknown senders
2. **Owner must have Microsoft connected** - The mailbox owner's OAuth token is used for syncing

## Enabling Sync

1. Go to **Settings → Mail Pro → Manage Mailbox List**
2. Open a mailbox
3. Set **Sync Mode**:
   - **Send messages only** - No incoming sync
   - **Send and receive from existing contacts** - Only sync from known partners
   - **All** - Sync all emails with routing rules
4. Set the **Owner** (must have Microsoft connected)
5. Configure routing (see below)
6. Save

## Routing Configuration

### Team Selection

Select a **Team** (alias) to route emails to:

- **CRM Team** → Creates Leads
- **Helpdesk Team** → Creates Tickets
- **Sales Team** → Creates Opportunities

### Contact Type Routing (All mode)

When using "All" sync mode, configure routing per contact type:

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

- **Internal domain:** Auto-detected from company email
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
3. **Scheduled Actions** - Check "Microsoft Graph: Fetch Incoming Mail"
