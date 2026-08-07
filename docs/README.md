# Mail Pro

Complete email integration for Odoo — send and receive with full control, on
Microsoft 365, Google Workspace or any IMAP/SMTP mailbox.

## What is Mail Pro?

Mail Pro connects your Odoo instance to your real mailboxes, so that:

- **Sending** goes out from the address you chose, not from `notifications@`
- **Receiving** files mail onto the right lead, ticket, order or contact
- **Shared mailboxes** work for team addresses (sales@, support@, info@)
- **Reply threading** keeps conversations together across mail clients

## Providers

| Provider | How it connects | Shared mailboxes |
|----------|-----------------|------------------|
| **Microsoft 365** | Graph API, OAuth 2.0 | Colleagues send as the address with their own sign-in (SendAs) |
| **Google Workspace** | Gmail API, OAuth 2.0 | The address is its own Workspace account |
| **IMAP/SMTP** | Server, login and password | The address is its own login |

One database can serve mailboxes on several providers at once.

## Key Features

| Feature | Description |
|---------|-------------|
| Send From dropdown | Choose which mailbox to send from in the composer |
| 2-way sync | Inbox and Sent Items sync automatically, every minute |
| Reply threading | Replies attach to the correct record |
| Personal mailboxes | Auto-created when users connect |
| Shared mailboxes | Team addresses visible to all users |
| Contact routing | Route incoming mail to a team so it creates leads or tickets |
| Internal domain filter | Your own internal email is never synced |
| Block list | Exclude specific contacts from sync |
| Mail Routing log | See where every email landed, and why |
| Triage queue | Review mail from senders who are not contacts yet |
| Link Coverage | Measure how much of your mail actually lands on a document |

## Security

- OAuth 2.0 with **delegated permissions only** where the provider offers it
- Tokens encrypted at rest
- No admin permissions required
- Internal domains must be configured before any mailbox may sync
- AI features are off by default, use your own API key, and never see message
  bodies or attachments

See [Security](security.md) for the full picture.

## Getting Started

1. [Installation](getting-started/installation.md) — add the module to your Odoo instance
2. Set up your provider — [Microsoft 365](getting-started/azure-setup.md),
   [Google Workspace](getting-started/google-setup.md) or
   [IMAP/SMTP](getting-started/imap-setup.md)
3. [User Setup](getting-started/user-setup.md) — connect accounts
4. [Mailboxes](configuration/mailboxes.md) — configure sending and sync

Once mail is flowing, [Where Mail Lands](configuration/where-mail-lands.md)
explains how to check it is going where you expect.

## Need Help?

- Check the [Troubleshooting](troubleshooting.md) guide
- Contact support at support@pantalytics.com
