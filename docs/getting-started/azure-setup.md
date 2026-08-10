# Microsoft 365 Setup

This guide walks you through creating a Microsoft Entra ID (Azure AD) app
registration for Mail Pro.

You only need this if your mail is hosted at Microsoft 365. For Google
Workspace see [Google Workspace Setup](google-setup.md); for any other host see
[IMAP/SMTP Setup](imap-setup.md).

## Step 1: Create App Registration

1. Go to <a href="https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade" target="_blank">Azure Portal → App Registrations</a>
2. Click **New registration**
3. Configure:
   - **Name:** `Odoo Mail Pro` (or your preference)
   - **Supported account types:** Accounts in this organizational directory only
   - **Redirect URI:** Web → copy the Callback URL from Odoo (Settings → Mail Pro → Setup Guide). The URL format is `https://your-odoo-domain.com/microsoft_oauth/callback`
4. Click **Register**

## Step 2: Note Application IDs

After registration, copy these values (you'll need them in Odoo):

- **Application (client) ID**
- **Directory (tenant) ID**

## Step 3: Create Client Secret

1. Go to **Certificates & secrets**
2. Click **New client secret**
3. Add description: `Odoo`
4. Select expiration (recommend 24 months)
5. Click **Add**
6. **Copy the secret value immediately** (it won't be shown again)

## Step 4: Configure API Permissions

1. Go to **API permissions**
2. Click **Add a permission**
3. Select **Microsoft Graph**
4. Select **Delegated permissions**
5. Add these permissions:

   **Required (personal mailbox):**
   - `Mail.ReadWrite` - Create drafts, read emails
   - `Mail.Send` - Send emails
   - `offline_access` - Refresh tokens
   - `User.Read` - User profile

   **Required for shared mailboxes:**
   - `Mail.ReadWrite.Shared` - Create drafts in shared mailbox
   - `Mail.Send.Shared` - Send from shared mailbox

6. Click **Grant admin consent** (requires Azure admin)

## Step 5: Configure in Odoo

1. Go to **Settings → Mail Pro**
2. Set **Email provider** to *Microsoft 365*
3. Enter:
   - **Client ID:** Your Application (client) ID
   - **Tenant ID:** Your Directory (tenant) ID
   - **Client Secret:** The secret value you copied
4. Save

## Next Steps

Configuration is complete. Proceed to [User Setup](user-setup.md) to connect Microsoft accounts.
