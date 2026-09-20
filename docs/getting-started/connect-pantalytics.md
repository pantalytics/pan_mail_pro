# Connect to Pantalytics

Mail Pro works on an Odoo instance that is connected to a Pantalytics account.
Until it is, the Settings page shows one button and nothing else: sending
through Odoo's own mail server keeps working, incoming mail is not synced and
no mailbox can be connected.

You need a Pantalytics account. If you do not have one yet, you create it on
the page the button opens, with your work email address. Colleagues who sign
in with the same email domain later can be added to the same workspace.

## Connect

1. Go to **Settings** and scroll to **Mail Pro**
2. Press **Connect to Pantalytics**. A new tab opens on `app.mailpro.pantalytics.com`
   with a short code already filled in; the same code is shown in Odoo
3. Sign in with your Pantalytics account, or create one
4. Check that the page names this Odoo instance, and press **Approve**
5. Press the button back to your Odoo. The Settings page now shows
   **Status: Active** and the setup checklist appears below it

No tab opened? The Settings page shows the link under the code. Approved but
Odoo still says it is waiting? Press **Check Approval**.

Only an administrator of the Pantalytics workspace can approve. If the page
says the Odoo instance is already connected to a workspace you do not
administer, ask an administrator of that workspace to approve instead.

## What it means

- **Status: Active, plan free.** During the beta every connected instance is
  on the free plan and no sending limit is enforced. The intended limits are
  25 mails a day on Free, 500 on Pro and 5,000 on Max; you will hear before any
  of them takes effect.
- **Once a day** the instance reports in and receives a signed answer. If
  Pantalytics cannot be reached, the last answer stays valid for 14 days;
  after that, incoming sync pauses until the next successful check. Sending
  never stops for a licence reason.
- **Disconnect** forgets the key. You can connect again at any time; the
  instance keeps its place in the workspace.

## What leaves your server

One request a day to `app.mailpro.pantalytics.com`, carrying: the database
id, the Mail Pro and Odoo version, how many accounts are connected and
whether sync is healthy. When you connect, the Odoo URL is sent too so the
approval page can name this instance. Never an address, a subject, a body or a
name. See [Security](../security.md).

## Problems

- **"Could not reach Pantalytics."** This server cannot make outgoing HTTPS
  requests to `app.mailpro.pantalytics.com`. Ask whoever runs the server.
- **"Pantalytics did not answer (HTTP 502)."** A bad minute on our side. Try
  again; the code stays valid for 15 minutes.
- **"This code is no longer valid."** The code expired or was used. Press
  **Connect to Pantalytics** for a new one.
- **A user sees "Connect this Odoo instance to Pantalytics" when connecting
  their mailbox.** The instance is not connected, or its connection lapsed.
  An administrator connects it under Settings, Mail Pro. Existing accounts
  can still reconnect.
