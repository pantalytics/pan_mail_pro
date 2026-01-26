# Outlook Pro

Complete Microsoft 365 email integration for Odoo - send and receive with full control.

## Installation as Git Submodule (Odoo.sh)

### 1. Add Submodule in Odoo.sh

1. In Odoo.sh, go to **Settings → Submodules**
2. Click **Add submodule**
3. Enter the SSH URL: `git@github.com:pantalytics/pan_outlook_pro.git`
4. Copy the **Public Key** that is displayed

### 2. Add Deploy Key in GitHub

1. Go to the source repo: `github.com/pantalytics/pan_outlook_pro`
2. Go to **Settings → Deploy keys**
3. Click **Add deploy key**
4. Paste the public key from Odoo.sh
5. Save

### 3. Add Submodule to Your Local Project

```bash
# Add submodule (use SSH URL directly for Odoo.sh compatibility)
git submodule add git@github.com:pantalytics/pan_outlook_pro.git

# Commit and push
git add .gitmodules pan_outlook_pro
git commit -m "Add pan_outlook_pro submodule"
git push
```

> **Note:** Use SSH URL (`git@github.com:...`) for Odoo.sh compatibility. HTTPS URLs won't work with deploy keys.

---

## Features

**Outgoing Email:**
- Send From dropdown in email composer
- Shared mailbox support (sales@, support@, etc.)
- Default mailbox per user
- Inline chatter and full composer support
- Correct Message-ID for reply threading

**Incoming Email:**
- Automatic sync from Microsoft 365 mailboxes (1 min interval)
- 2-way sync: Inbox and Sent Items
- Reply threading via In-Reply-To headers
- Auto-create contacts with correct name/email
- Activity creation for team assignment
- Skip old emails on first sync

**Security:**
- OAuth 2.0 authentication with Microsoft
- Token encryption at rest (Fernet)

## Setup Instructions

### 1. Azure App Registration

1. Go to [Azure App Registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
2. Click "New registration"
3. Fill in:
   - **Name**: `Odoo Outlook Pro`
   - **Supported account types**: `Accounts in this organizational directory only`
   - **Redirect URI**: Leave empty for now (we'll add it later)
4. Click "Register"

### 2. Get Client ID and Tenant ID

1. On the app overview page, copy:
   - **Application (client) ID**
   - **Directory (tenant) ID**

### 3. Create Client Secret

1. Go to "Certificates & secrets"
2. Click "New client secret"
3. Add description: `Odoo Integration`
4. Set expiry (e.g., 24 months)
5. Click "Add"
6. **Copy the secret value immediately** (you won't be able to see it again)

### 4. Configure API Permissions

1. Go to "API permissions"
2. Click "Add a permission"
3. Select "Microsoft Graph" → "Delegated permissions"
4. Add these permissions:
   - `openid`
   - `profile`
   - `email`
   - `offline_access`
   - `User.Read`
   - `Mail.Send`
   - `Mail.Send.Shared`
   - `Mail.Read`
   - `Mail.Read.Shared`
5. Click "Add permissions"
6. Click "Grant admin consent" (if you're admin)

### 5. Configure in Odoo

1. In Odoo, go to **Settings** → **General Settings**
2. Scroll to **Outlook Pro** section
3. Fill in:
   - **Client ID**: from step 2
   - **Client Secret**: from step 3
   - **Tenant ID**: from step 2
4. Copy the **Redirect URI** shown in the instructions
5. Click "Save"

### 6. Add Redirect URI in Azure

1. Go back to Azure App Registration
2. Go to "Authentication"
3. Click "Add a platform" → "Web"
4. Paste the redirect URI from Odoo (e.g., `https://your-odoo.odoo.com/microsoft_oauth/callback`)
5. Click "Configure"

### 7. Connect Your Microsoft Account

1. Go to My Profile → Preferences → Email tab
2. Click "Connect Microsoft Account"
3. Sign in with your Microsoft account
4. Grant permissions when asked
5. You'll be redirected back to Odoo with a success message

### 8. Set Default Mailbox

1. In My Profile → Preferences → Email tab
2. Select your default "Send From" mailbox
3. Save

### 9. Configure Incoming Email Sync

1. Go to Settings → Outlook Pro → Manage Mailbox List
2. Open a mailbox (or create one)
3. Select a **Sync Mode**:
   - **No sync (outgoing only)** - Only use this mailbox for sending
   - **Received emails only** - Sync inbox to Odoo
   - **Received + Sent from Outlook** - Full 2-way sync (including replies sent via Outlook)
4. Select **Sync User** (must have Microsoft connected)
5. Optionally enable **Create activity for new emails**
6. Save

Emails will sync automatically every minute. On first sync, only the timestamp is set - no old emails are imported.

---

## Known Limitations

### Cannot Query SendAs Permissions via Graph API

**Issue:** Microsoft Graph API does not provide an endpoint to query which shared mailboxes a user has SendAs permission for.

**Impact:** We cannot automatically show users only the mailboxes they have access to. Instead:
- Admin adds all available mailboxes in Odoo
- User sees all mailboxes in dropdown
- Azure validates permission when sending (returns error if no access)

---

## Security

### Delegated Permissions (Least Privilege)

This module uses **Delegated Permissions only** - the app acts on behalf of the signed-in user, not as an administrator.

| Type | Description | Risk |
|------|-------------|------|
| **Delegated** (we use this) | App acts on behalf of signed-in user | Low - user can only do what they're already allowed to do |
| Application | App acts independently, without user | High - access to ALL mailboxes in tenant |

**Required permissions:**

| Permission | Type | Purpose |
|------------|------|---------|
| `Mail.Send` | Delegated | Send from user's own mailbox |
| `Mail.Send.Shared` | Delegated | Send from shared mailboxes where user has SendAs permission |
| `Mail.Read` | Delegated | Fetch Message-ID after sending (for reply threading) |
| `Mail.Read.Shared` | Delegated | Fetch Message-ID from shared mailbox sent items |
| `User.Read` | Delegated | Basic profile info during OAuth login |
| `offline_access` | Delegated | Refresh tokens for continued access |

**No Application Permissions needed** - this module has no admin-level access to your tenant.

### Why This Is Secure

1. **No central access to all mailboxes** - Each user only accesses their own mailboxes
2. **Double authorization** - User must complete OAuth flow AND have Exchange SendAs permission
3. **Azure/Exchange is the authority** - Odoo cannot bypass Microsoft's permission checks
4. **Easy revocation**:
   - Remove Exchange SendAs permission → immediate loss of access
   - User revokes app consent in Azure → tokens invalidated
   - Admin clicks "Disconnect" in Odoo → tokens deleted

### Token Encryption

All OAuth tokens and secrets are encrypted at rest using Fernet symmetric encryption:
- Access tokens (per user)
- Refresh tokens (per user)
- Client secret (system-wide)

The encryption key is auto-generated on first use and stored in `ir.config_parameter`. This provides defense-in-depth on top of Odoo.sh's database encryption.

See [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md) for detailed security rationale.

---

## Development

**Addon Name**: `pan_outlook_pro`

**Models**:
- `res.config.settings` - OAuth configuration
- `res.users` - Token storage per user
- `x_microsoft.mailbox` - Mailbox configuration
- `microsoft.graph.client` - Graph API helper
- `microsoft.incoming.mail.processor` - Incoming email sync
- `microsoft.oauth.wizard` - OAuth connection wizard
- `mail.mail` - Email sending override
- `mail.compose.message` - Composer integration

**Controllers**:
- `/microsoft_oauth/callback` - OAuth callback handler

**Cron Jobs**:
- `Microsoft Graph: Fetch Incoming Mail` - Runs every 1 minute

---

## Troubleshooting

### Reply threading not working
The module fetches Microsoft's Message-ID after sending and stores it in Odoo. This allows Gmail/Outlook replies to be correctly threaded. If threading fails:
1. Check logs for "Retrieved Microsoft Message-ID"
2. Verify the email has `In-Reply-To` header pointing to that ID

### Emails not syncing
1. Check Settings → Technical → Scheduled Actions → "Microsoft Graph: Fetch Incoming Mail"
2. Verify the mailbox has a sync mode other than "No sync"
3. Check the "Sync User" has a valid Microsoft connection
4. Check logs for `[Incoming Mail]` entries

### "0 mailbox(es)" in logs
The mailbox configuration is incomplete. Ensure:
- Sync Mode is set to "Received emails only" or "Received + Sent from Outlook"
- Sync User is set to a user with Microsoft OAuth connected

### Old emails imported on first sync
This shouldn't happen - the first sync only sets the timestamp. If it does, check `x_last_sync_date` on the mailbox.
