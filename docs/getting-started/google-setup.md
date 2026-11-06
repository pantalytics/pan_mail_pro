# Google Workspace Setup

Mail Pro signs in to Gmail through an OAuth client in your own Google Cloud
project. Making one takes about ten minutes and needs a Google Workspace
administrator. You do it once; after that everybody connects their mailbox
with their own Google sign-in.

You only need this if your mail is hosted at Google Workspace. For Microsoft 365
see [Microsoft 365 Setup](azure-setup.md); for any other host see
[IMAP/SMTP Setup](imap-setup.md). A personal @gmail.com account cannot be
connected: Mail Pro on Google is for Workspace accounts only.

Before you start, finish step 1 of **Settings → Mail Pro** (connect this Odoo
to Pantalytics).

## Step 1: Pick Gmail in Odoo

1. Go to **Settings → Mail Pro** and press the arrow on step 2, *Email provider*.
   The provider page opens on Microsoft 365.
2. Set **Provider** to *Gmail*.
3. Copy the **Callback URL** it shows. It looks like
   `https://your-odoo-domain.com/google_oauth/callback`.

Leave this tab open; you come back to it in Step 5.

## Step 2: Create a Google Cloud project

1. Open <a href="https://console.cloud.google.com/projectcreate" target="_blank">Google Cloud Console → New project</a>,
   signed in with your Workspace administrator account
2. Name it `Odoo Mail Pro`, keep your organization as its location, and press **Create**
3. Check that the new project is selected in the picker at the top

## Step 3: Turn on the Gmail API

1. Open <a href="https://console.cloud.google.com/apis/library/gmail.googleapis.com" target="_blank">Gmail API</a>
   in the API Library
2. Press **Enable**

Skip this and the sign-in still works, but every mail Mail Pro sends or reads
afterwards fails.

## Step 4: Set up the sign-in screen and the client

1. Open <a href="https://console.cloud.google.com/auth/overview" target="_blank">Google Auth Platform</a>
   and press **Get started**
2. **App information:** app name `Odoo Mail Pro`, and your own address as
   **User support email**
3. **Audience:** choose **Internal**. Only accounts in your Workspace can then
   sign in, which is what Mail Pro needs, and Google does not review the app
4. **Contact information:** your address. Agree to the policy and press **Create**
5. Go to **Clients** and press **Create client**
6. **Application type:** *Web application*. Name: `Odoo Mail Pro`
7. Under **Authorized redirect URIs**, press **Add URI** and paste the Callback
   URL from Step 1
8. Press **Create**, then copy the **Client ID** and the **Client secret**

Google shows the client secret only now. Copy it, or press **Download JSON**,
before you close the dialog. Lost it? Open the client, add a new secret, and
use that one.

An Internal app needs nothing under **Data Access**: Google does not list
scopes on the sign-in screen for an app that only your own Workspace uses.

## Step 5: Enter the client in Odoo

1. Back on the provider page, paste the **Client ID** and **Client secret**
2. Press **Save**
3. Press **Sign in with Google** and sign in with your own Workspace account

Step 2 of Settings, Mail Pro turns green. Your colleagues each connect their
own mailbox next: see [User Setup](user-setup.md).

## What Mail Pro asks for

| Scope | Why |
|-------|-----|
| `openid`, `email` | Which Workspace account just signed in |
| `https://www.googleapis.com/auth/gmail.modify` | Read, label and send mail, and keep drafts |

`gmail.modify` covers sending, so Mail Pro does not ask for `gmail.send` as
well. It never asks for `https://mail.google.com/`, which would also allow
deleting mail for good.

## Shared addresses on Gmail

On Microsoft 365 a colleague sends *as* `sales@` with their own sign-in. On
Google, `sales@` is a Workspace user of its own, with its own password, and it
signs in as itself. A Google Group cannot sign in, so make `sales@` a user,
not a group.

1. Add the mailbox under **Settings → Mail Pro**, step 4, with no **Owner**
2. Open it. It says it is not signed in yet: press **Sign in as this address**
3. Google asks which account to use. Choose **Use another account** and sign
   in as `sales@`

Signed in as anybody else, Mail Pro stores nothing and says which address it
expected. Only a mailbox manager sees the button.

## Next Steps

Proceed to [User Setup](user-setup.md) to connect accounts.
