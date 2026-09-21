# Connect to Pantalytics

Mail Pro works on an Odoo instance that is connected to a Pantalytics account.
Until it is, the Settings page shows one button and nothing else: sending
through Odoo's own mail server keeps working, incoming mail is not synced and
no mailbox can be connected.

You need a Pantalytics account. If you do not have one yet, you create it on
the page the button opens, or beforehand at
<a href="https://app.mailpro.pantalytics.com/start" target="_blank">app.mailpro.pantalytics.com/start</a>,
with your work email address. Forgotten password and the verification mail
are handled on that sign-in page. Colleagues can be added to the same
workspace afterwards, under **Members**.

## Connect

1. Go to **Settings** and scroll to **Mail Pro**
2. Press **Connect to Pantalytics**. A new tab opens on `app.mailpro.pantalytics.com`
   with a short code already filled in; the same code is shown in Odoo
3. Sign in with your Pantalytics account, or create one
4. Check that the page names this Odoo instance, and press **Approve**
5. Press the button back to your Odoo. The Settings page now shows
   **Status: Active** and the setup checklist appears below it

No tab opened? The Settings page shows the link under the code. Approved but
Odoo still says it is waiting? Press **Check Approval**. Connected, but the
page still says incoming mail is not synced? Press **Check again**: the
instance asks Pantalytics once more, which it also does by itself every ten
minutes.

Only an administrator of the Pantalytics workspace can approve. If the page
says the Odoo instance is already connected to a workspace you do not
administer, ask an administrator of that workspace to approve instead.

## What it means

- **Status: Active, plan free.** A connected instance starts on Free: 100
  mails a day. Pro is 1,000 a day and Max 5,000, one button on the
  **Billing** page of your workspace at `app.mailpro.pantalytics.com`; every
  Odoo instance in the workspace gets the plan's limit within a day. Over the
  limit, mail waits until the next day, it is never refused. The limit is not
  enforced by this version of the module yet; you will hear before it is.
- **Once a day** the instance reports in and receives a signed answer. If
  Pantalytics cannot be reached, the last answer stays valid for 14 days;
  after that, incoming sync pauses until the next successful check. Sending
  never stops for a licence reason.
- **Disconnect** forgets the key. You can connect again at any time; the
  instance keeps its place in the workspace.

## What leaves your server

One request a day to `app.mailpro.pantalytics.com`, carrying: the database
id, the Mail Pro and Odoo version, how many accounts are connected, whether
sync is healthy, and for the last 24 hours how many mails were linked to a
document, to a contact only, or to nothing, per matching rule how often it
decided and how often somebody overruled it, and how many conversations were
linked by hand. Counts and rule names. When you connect, the Odoo URL is sent
too so the approval page can name this instance. Never an address, a subject,
a body or a name. See [Security](../security.md).

## Help improve Mail Pro

Off unless an administrator of your Pantalytics workspace turns it on, under
**Settings** at `app.mailpro.pantalytics.com`. When it is on, the Mail Pro
inbox in the browser reports which screens, tabs and buttons are used and
records the session with every word, every field and every attribute masked:
a wireframe, never a mail, an address, a name or a subject. It covers Mail
Pro's own screens and nothing else in your Odoo, and it goes to Pantalytics
only, never to a third party directly. Recordings are kept 30 days.

An Odoo administrator can refuse it for one instance under **Settings, Mail
Pro, Pantalytics Account**, with **Not on this Odoo instance**. Telling the
people who use your Odoo is your part.

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
