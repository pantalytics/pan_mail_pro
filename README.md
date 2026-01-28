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
- Reply threading via In-Reply-To headers
- Auto-create contacts for unknown senders

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

### 1. Azure App Registration

1. Go to [Azure App Registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
2. Click **New registration**
3. Configure:
   - **Name**: `Odoo Outlook Pro`
   - **Supported account types**: `Accounts in this organizational directory only`
4. Click **Register**
5. Copy **Application (client) ID** and **Directory (tenant) ID**

### 2. Create Client Secret

1. Go to **Certificates & secrets**
2. Click **New client secret**
3. Set expiry (e.g., 24 months)
4. **Copy the secret value immediately**

### 3. Configure API Permissions

1. Go to **API permissions** → **Add a permission**
2. Select **Microsoft Graph** → **Delegated permissions**
3. Add:
   - `openid`, `profile`, `email`, `offline_access`
   - `User.Read`
   - `Mail.Send`, `Mail.Send.Shared`
   - `Mail.Read`, `Mail.Read.Shared`
4. Click **Grant admin consent**

### 4. Configure in Odoo

1. Go to **Settings** → scroll to **Outlook Pro**
2. Enter Client ID, Client Secret, and Tenant ID
3. Copy the **Redirect URI** shown
4. Click **Save**

### 5. Add Redirect URI in Azure

1. In Azure, go to **Authentication**
2. Click **Add a platform** → **Web**
3. Paste the redirect URI from Odoo
4. Click **Configure**

### 6. Test Configuration

1. Click **Test Configuration** in Odoo settings
2. Should show "Tenant ID verified"

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

1. Open a mailbox
2. Select **Sync Mode**:
   - **No sync** - Outgoing only
   - **Received emails only** - Sync inbox
   - **Received + Sent from Outlook** - Full 2-way sync
3. Select **Sync User** (must have Microsoft connected)
4. Optionally enable **Create activity for new emails**
5. Save

Emails sync automatically every minute. First sync only sets timestamp - no historical emails imported.

---

## Troubleshooting

### Reply threading not working

Check logs for "Retrieved Microsoft Message-ID". The module fetches Microsoft's Message-ID after sending for correct threading.

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

## Development

See [ARCHITECTURE.md](ARCHITECTURE.md) for:
- Module structure
- Email flow diagrams
- Design decisions
- API documentation
