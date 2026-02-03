# Outlook Pro

Complete Microsoft 365 email integration for Odoo - send and receive with full control.

## Features

**Outgoing Email:**
- Send From dropdown in email composer
- Personal, shared, and notification mailbox support
- Auto-create personal mailbox on Microsoft connect
- Default mailbox per user

**Incoming Email:**
- Automatic sync from Microsoft 365 mailboxes (1 min interval)
- 2-way sync: Inbox and Sent Items
- Reply threading via In-Reply-To headers + Microsoft conversationId fallback
- Historical email sync with configurable start date
- Known partners filter: only sync emails from existing contacts
- "All" sync mode with per-contact routing rules
- Per-contact block list to exclude specific senders
- New emails create CRM Leads with activity for mailbox owner

**Security:**
- OAuth 2.0 with delegated permissions only (least privilege)
- Token encryption at rest

---

## Installation

### As Git Submodule (Odoo.sh)

1. In Odoo.sh, go to **Settings → Submodules**
2. Click **Add submodule**
3. Enter: `git@github.com:pantalytics/pan_outlook_pro.git`
4. Copy the **Public Key** and add it as Deploy Key in GitHub

```bash
# Local: add submodule
git submodule add git@github.com:pantalytics/pan_outlook_pro.git addons/pan_outlook_pro
git commit -m "Add pan_outlook_pro submodule"
git push
```

---

## Setup

After installing the module, go to **Settings** → scroll to **Outlook Pro**.

The settings page contains step-by-step instructions for:
1. Creating an Azure App Registration
2. Configuring API permissions and granting admin consent
3. Connecting your Microsoft account
4. Setting up mailboxes

Follow the instructions in Odoo - they include links to the correct Azure Portal pages and explain each step.

---

## User Setup

### Connect Microsoft Account

1. Go to **My Profile** → **Preferences** → **Email** tab
2. Click **Connect Microsoft Account**
3. Sign in and grant permissions
4. A personal mailbox is automatically created

### Set Default Mailbox

1. In **My Profile** → **Preferences** → **Email** tab
2. Select your default **Send From** mailbox
3. Save

**Note:** If your Microsoft account is not connected or no default mailbox is set, you'll see a warning banner when opening the email composer.

---

## Mailbox Configuration

Go to **Settings** → **Outlook Pro** → **Manage Mailbox List**

### Mailbox Types

| Type | Description |
|------|-------------|
| **Personal** | User's own mailbox. Auto-created on connect. Only visible to owner. |
| **Shared** | Team mailbox (sales@, support@). Visible to all users. Each user sends with own OAuth. |
| **Notification** | System emails. One designated sender user. |

### Incoming Email Sync

**Prerequisite:** Create a **Notification mailbox** first (required for handling emails from external authors).

1. Open a mailbox
2. Select **Sync Mode**:
   - **Send messages only** - Outgoing only (no sync)
   - **Send and receive messages from existing contacts** - 2-way sync, only from known partners
   - **All** - 2-way sync with configurable routing rules per contact type
3. Configure **Routing**:
   - Select a **Team** (alias) to route emails to (e.g., Helpdesk, Sales)
   - Emails create tickets/leads in the selected team
4. Set the **Owner** (must have Microsoft connected)
5. Optionally set **Sync Start Date** for historical email import
6. Save

**Sync behavior:**
- Internal domain is auto-detected from company email or mailboxes
- Enable "Exclude Internal Emails" in Settings → Outlook Pro to filter internal traffic
- Internal users (employees with Odoo accounts) are always excluded
- Emails sync automatically every minute
- Set a sync start date to import historical emails (default: sync from now)

**Per-contact block list:**
- Go to a contact's form view → Email Sync tab
- Enable "Block Email Sync" to exclude that contact from all mailbox sync

---

## Troubleshooting

### Reply threading not working

Threading uses two methods:
1. **In-Reply-To header** - Standard email threading (works for Inbox)
2. **Microsoft conversationId** - Fallback when headers unavailable (works for Sent Items)

Check logs for "Threading reply to" entries. If replies go to the wrong record, ensure the original email was synced first (conversationId must be stored).

### Emails not syncing

1. Check **Settings** → **Technical** → **Scheduled Actions** → "Microsoft Graph: Fetch Incoming Mail"
2. Verify mailbox has sync mode enabled
3. Verify Sync User has Microsoft connected
4. Check logs for `[Incoming Mail]` entries

### "0 mailbox(es)" in logs

Mailbox configuration incomplete:
- Sync Mode must be set
- Sync User must be set and have Microsoft OAuth connected

### Permission denied when sending

User lacks SendAs permission on the mailbox in Microsoft 365. Configure this in Exchange Admin Center.

---

## Security

This module uses **Delegated Permissions only** - the app acts on behalf of the signed-in user, not as an administrator.

| Aspect | Implementation |
|--------|----------------|
| Authentication | OAuth 2.0 with Microsoft Entra ID |
| Permissions | Delegated only (no admin access) |
| Token storage | Encrypted at rest (Fernet) |
| Shared mailbox | User needs M365 SendAs permission |

See [ARCHITECTURE.md](ARCHITECTURE.md) for technical details.

---

## Roadmap

### v2.0 - AI-Assisted Triage
- Sync emails from unknown senders
- AI suggests contact qualification (ICP match)
- Human approves, future emails auto-routed
- Customer summaries per company

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed roadmap.

---

## Development

See [ARCHITECTURE.md](ARCHITECTURE.md) for:
- Module structure
- Email flow diagrams
- Design decisions
- API documentation

### Running Tests

```bash
cd .local
docker-compose stop odoo
docker-compose run --rm odoo python -m odoo -c /etc/odoo/odoo.conf \
  -d test_db -u pan_outlook_pro --test-enable --test-tags=pan_outlook_pro --stop-after-init
docker-compose start odoo
```

Tests cover: internal domain filtering, duplicate detection, partner matching, alias routing.
