# Outlook Pro

Complete Microsoft 365 email integration for Odoo - send and receive with full control.

## What is Outlook Pro?

Outlook Pro connects your Odoo instance to Microsoft 365, enabling:

- **Send emails** via Microsoft Graph API from any Odoo form
- **Receive emails** with automatic sync to CRM Leads or Helpdesk Tickets
- **Shared mailboxes** for team email addresses (sales@, support@)
- **Reply threading** that keeps conversations together

## Key Features

| Feature | Description |
|---------|-------------|
| Send From dropdown | Choose which mailbox to send from in the composer |
| 2-way sync | Inbox and Sent Items sync automatically |
| Reply threading | Replies attach to the correct record |
| Personal mailboxes | Auto-created when users connect |
| Shared mailboxes | Team addresses visible to all users |
| Contact routing | Route emails based on sender type |
| Block list | Exclude specific contacts from sync |

## Security

- OAuth 2.0 with **delegated permissions only** (least privilege)
- Tokens encrypted at rest
- No admin permissions required

## Getting Started

1. [Installation](getting-started/installation.md) - Add the module to your Odoo instance
2. [Azure Setup](getting-started/azure-setup.md) - Create the Microsoft app registration
3. [User Setup](getting-started/user-setup.md) - Connect your Microsoft account

## Need Help?

- Check the [Troubleshooting](troubleshooting.md) guide
- Contact support at support@pantalytics.com
