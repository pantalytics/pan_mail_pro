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
   - **Redirect URI:** Web → copy the Callback URL from Odoo (Settings → Mail Pro, the arrow on step 1). The URL format is `https://your-odoo-domain.com/microsoft_oauth/callback`
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
6. **Copy the Value immediately** (it won't be shown again)

Azure shows two columns here: **Value** and **Secret ID**. Odoo needs the
**Value**. The Secret ID is a different string and is never used.

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

   **Optional** (skip on a first setup; see *Telling a user account from a
   shared mailbox* below):
   - `MailboxSettings.Read` - Read whether an address is a user, a shared mailbox or a room
   - `User.ReadBasic.All` - Tell a mailbox's own address from an alias on it

6. Click **Grant admin consent** (requires Azure admin)

## Step 5: Configure in Odoo

1. Go to **Settings → Mail Pro** and press the arrow on step 1, *Email Provider*
2. Create the provider row and set **Provider** to *Microsoft 365*
3. Enter the three values under the same names Azure gives them:
   - **Application (client) ID**
   - **Client Secret Value** (the Value from Step 3, not the Secret ID)
   - **Directory (tenant) ID**
4. Save. A dialog connects to Microsoft and verifies the three values. If
   Azure refuses them, it names the field to fix.
5. Click **Sign in** in that dialog. This walks the real consent screen, which
   is the only check that also covers the Callback URL, the permissions from
   Step 4 and whether your tenant lets users consent at all.

## Telling a user account from a shared mailbox (optional)

Mail Pro checks on its own whether each sign-in can read a configured mailbox
and whether its sends go through; that needs none of the permissions below.
What it cannot tell without them is *what* an address is: a shared mailbox, a
person's own mailbox, or an alias. The Kind column on the mailbox list then
says *Unknown*, and nothing else changes.

To have it say so:

1. Add `MailboxSettings.Read` and `User.ReadBasic.All` (delegated) to the
   API permissions of the app registration and grant admin consent again
2. In Odoo, under **Settings → Technical → System Parameters**, create
   `pan_mail_pro.graph_inspect_scopes` with value `True`
3. Everyone presses Disconnect, then Connect mailbox, under My Preferences →
   Mail Pro. A grant from before the change lacks the two permissions, and
   Mail Pro does not make a call the grant cannot answer

Do step 1 before step 2. With the parameter set and the permissions missing,
a tenant that does not allow user consent shows *needs admin approval* on
every connect.

## Next Steps

Configuration is complete. Proceed to [User Setup](user-setup.md) to connect Microsoft accounts.
