# Azure Setup

This guide walks you through creating a Microsoft Entra ID (Azure AD) app registration for Outlook Pro.

## Step 1: Create App Registration

1. Go to [Azure Portal → App Registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
2. Click **New registration**
3. Configure:
   - **Name:** `Odoo Outlook Pro` (or your preference)
   - **Supported account types:** Accounts in this organizational directory only
   - **Redirect URI:** Web → `https://your-odoo-domain.com/microsoft/callback`
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
   - `Mail.ReadWrite`
   - `Mail.Send`
   - `offline_access`
   - `User.Read`
6. Click **Grant admin consent** (requires Azure admin)

## Step 5: Configure in Odoo

1. Go to **Settings → Outlook Pro**
2. Enter:
   - **Client ID:** Your Application (client) ID
   - **Tenant ID:** Your Directory (tenant) ID
   - **Client Secret:** The secret value you copied
3. Save

## Next Steps

Configuration is complete. Proceed to [User Setup](user-setup.md) to connect Microsoft accounts.
