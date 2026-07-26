# Mailboxes

Mailboxes define which email addresses can be used to send and receive emails in Odoo.

## Accessing Mailbox Configuration

Go to **Settings → Mail Pro → Manage Mailbox List**

## Mailbox Types

### Personal Mailbox

- **Purpose:** Individual user's email
- **Visibility:** Only the owner
- **OAuth:** Uses owner's token
- **Creation:** Auto-created when user connects Microsoft account

### Shared Mailbox

- **Purpose:** Team email addresses (sales@, support@, info@)
- **Visibility:** All users
- **OAuth:** Each sender uses their own token
- **Requirement:** Users need SendAs permission in Microsoft 365

### Notification Mailbox

- **Purpose:** System notifications, auto-replies
- **Visibility:** All users
- **OAuth:** Uses designated owner's token
- **Required for:** Incoming email sync (handles emails from unknown senders)

## Creating a Mailbox

1. Click **Create**
2. Fill in:
   - **Email Address:** The Microsoft 365 email address
   - **Display Name:** Shown in the Send From dropdown
   - **Type:** Personal, Shared, or Notification
   - **Owner:** User whose OAuth is used (Personal/Notification only)
3. Save

## Mailbox Settings

| Field | Description |
|-------|-------------|
| Email Address | The Microsoft 365 email to send from |
| Display Name | Friendly name shown in dropdowns |
| Type | Personal, Shared, or Notification |
| Owner | User whose OAuth token is used |
| Sync Mode | Controls incoming email sync |
| Team | Alias for routing incoming emails |

## Next Steps

To enable incoming email sync, see [Incoming Email Sync](incoming-sync.md).
