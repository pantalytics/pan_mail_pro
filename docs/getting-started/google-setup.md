# Google Workspace Setup

This guide walks you through creating a Google Cloud OAuth client so Mail Pro
can send and receive through the Gmail API.

You only need this if your mail is hosted at Google Workspace. For Microsoft 365
see [Azure Setup](azure-setup.md); for any other host see
[IMAP/SMTP Setup](imap-setup.md).

## Step 1: Create a Google Cloud project

1. Go to <a href="https://console.cloud.google.com/projectcreate" target="_blank">Google Cloud Console → New Project</a>
2. Give it a name such as `Odoo Mail Pro` and create it
3. Make sure the new project is selected in the project picker at the top

## Step 2: Enable the Gmail API

1. Go to **APIs & Services → Library**
2. Search for **Gmail API**
3. Click **Enable**

Nothing works until this is done — the OAuth screen will succeed and every call
afterwards will fail.

## Step 3: Configure the consent screen

1. Go to **APIs & Services → OAuth consent screen**
2. Choose **Internal**

   Internal means only accounts in your own Workspace can grant consent, which
   is exactly what this module needs. It also means Google does not put your app
   through the CASA security assessment that the scopes below would otherwise
   require. Choose **External** only if you know why you need it.
3. Fill in the app name, support email and developer contact
4. Save

## Step 4: Add the scopes

On the **Scopes** step, add these four:

| Scope | Why |
|-------|-----|
| `openid` | Identifies the user during the callback |
| `email` | Reads the address that just signed in |
| `https://www.googleapis.com/auth/gmail.modify` | Read incoming mail and label it |
| `https://www.googleapis.com/auth/gmail.send` | Send mail |

`gmail.modify` and `gmail.send` are restricted scopes. Mail Pro does not ask for
`https://mail.google.com/` (full mailbox control), because it never needs to
delete anything.

## Step 5: Create the OAuth client

1. Go to **APIs & Services → Credentials**
2. Click **Create Credentials → OAuth client ID**
3. Application type: **Web application**
4. Under **Authorized redirect URIs**, paste the callback URL that Odoo shows in
   **Settings → Mail Pro** once you have picked Google Workspace as the provider.
   The format is `https://your-odoo-domain.com/google_oauth/callback`
5. Click **Create** and copy the **Client ID** and **Client secret**

The redirect URI has to match character for character, including `https` and any
trailing path. A mismatch shows up as `redirect_uri_mismatch` on the consent
screen.

## Step 6: Configure in Odoo

1. Go to **Settings → Mail Pro**
2. Set **Email provider** to *Google Workspace*
3. Enter the **Client ID** and **Client Secret**
4. Save

Each provider keeps its own credentials, so switching the provider dropdown
later loses nothing — and one database can serve mailboxes on several providers
at once.

## Shared addresses on Gmail

A Gmail shared mailbox works differently from a Microsoft 365 one. On Microsoft
365 a colleague sends *as* `sales@` using their own token and SendAs rights. On
Google, `sales@` is a real Workspace account with its own credentials and no
owner.

So to set one up: sign in as that address when connecting it, and leave **Odoo
User** empty on the resulting account. Mail Pro then uses the address's own
credentials rather than borrowing anyone's.

## Next Steps

Proceed to [User Setup](user-setup.md) to connect accounts.
