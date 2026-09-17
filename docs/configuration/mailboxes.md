# Mailboxes

Mailboxes define which email addresses can be used to send and receive emails in Odoo.

## Accessing Mailbox Configuration

Go to **Settings → Technical → Email → Mail Pro → Mailboxes** (the arrow on step 3 of the checklist)

## Mailbox Types

### Personal Mailbox

- **Purpose:** Individual user's email
- **Visibility:** Only the owner
- **Credentials:** the owner's own sign-in, or on IMAP/SMTP the account's login and password
- **Creation:** Auto-created when the user connects their account
  (IMAP/SMTP: created by an administrator, alongside the account)

### Shared Mailbox

- **Purpose:** Team email addresses (sales@, support@, info@)
- **Visibility:** All users
- **Credentials (Microsoft 365):** each sender uses their own token, and needs
  SendAs permission in Microsoft 365
- **Credentials (Gmail, IMAP/SMTP):** the address has its own account, so the
  mailbox needs no owner - give the address credentials of its own

### The notification mailbox

Not a third type: a tick box, **Notification Mailbox**, on the one mailbox
that sends the system email (user invitations, password resets, activity
reminders). It sends with its owner's credentials and is required before any
mailbox may sync.

## Providers

Every mailbox names the provider that services it:

| Provider | Credentials |
|----------|-------------|
| Microsoft 365 | OAuth 2.0, connected by the user |
| Gmail | OAuth 2.0, connected by the user (a shared address is authorized once, on its own) |
| IMAP / SMTP | Server, login and password, entered by an admin |

### IMAP / SMTP mailboxes

There is no consent screen, so the credentials are entered once per address:

1. Go to **Settings → Technical → Email → Mail Pro → Email Accounts** and create an account.
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
   - **Mailbox:** The address, as the provider knows it. It is also what the
     Send From dropdown shows, so there is no separate display name
   - **Type:** Personal or Shared
   - **Owner:** The user whose credentials the mailbox sends with (Personal,
     and Shared on Microsoft 365)
3. Save
4. Press **Send Test Email** to prove the address can actually send

## Mailbox Settings

| Field | Description |
|-------|-------------|
| Mailbox | The address to send from, and the label in every dropdown |
| Type | Personal or Shared |
| Notification Mailbox | Ticked on the one mailbox that sends the system email |
| Owner | The user whose credentials are used |
| Receiving / Sending | Which folders are synced; replies always are |
| Team | Alias for routing incoming emails |

## Next Steps

To enable incoming email sync, see [Incoming Email Sync](incoming-sync.md).
