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
- **Credentials (Microsoft 365):** each sender uses their own token, and needs
  SendAs permission in Microsoft 365
- **Credentials (Gmail, IMAP/SMTP):** the address has its own account, so the
  mailbox needs no owner - give the address credentials of its own

### Notification Mailbox

- **Purpose:** System notifications, auto-replies
- **Visibility:** All users
- **OAuth:** Uses designated owner's token
- **Required for:** Incoming email sync (handles emails from unknown senders)

## Providers

Every mailbox names the provider that services it:

| Provider | Credentials |
|----------|-------------|
| Microsoft 365 | OAuth 2.0, connected by the user |
| Gmail | OAuth 2.0, connected by the user (a shared address is authorized once, on its own) |
| IMAP / SMTP | Server, login and password, entered by an admin |

### IMAP / SMTP mailboxes

There is no consent screen, so the credentials are entered once per address:

1. Go to **Settings → Technical → Email → Email Accounts** and create an account.
2. Set **Provider** to *IMAP / SMTP* and fill in the email address. Known
   hosters (Soverin) fill in their own servers automatically.
3. Enter the IMAP server, the SMTP server, the login (defaults to the address)
   and the password.
4. Press **Test Connection**. Both halves are checked - a mailbox that can read
   but not send is not usable.
5. Leave **Odoo User** empty for a shared address such as `info@`; set it for a
   person's own mailbox.
6. Create the mailbox itself with the same address and provider *IMAP / SMTP*.

Mail sent from Odoo is filed in the Sent folder, which is detected from the
server. If your server names it something unusual, set **Sent Folder** on the
account.

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
