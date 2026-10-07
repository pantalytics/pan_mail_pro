# Troubleshooting

## Sending Issues

### "… cannot send from …" (Microsoft 365)

**Cause:** The sign-in named in the message lacks a delegation on the shared
mailbox in Microsoft 365. Sending from a shared address needs two: **Full
Access** and **Send As**.

The address named is the one the user is connected as (**My Preferences →
Mail Pro → Connected as**), which is not always the address on their Odoo
user. Grant the rights to that one.

**Solution:**
1. Go to [Exchange Admin Center](https://admin.exchange.microsoft.com)
2. Navigate to **Recipients → Mailboxes**
3. Select the shared mailbox
4. **Delegation → Read and manage (Full Access)** → Add the user
5. **Delegation → Send As** → Add the user

### "… cannot read …", "There is no mailbox at …", "… is a user account"

Since 19.0.28 Mail Pro asks the provider what a configured address is and
whether the sign-in that serves it may read it, once an hour and at every
sign-in. What it found is the sentence on the mailbox form and on step 4 of
Settings → Mail Pro; the table behind it is **Settings → Technical → Email →
Mail Pro → Mailbox access**, one line per sign-in and mailbox.

| The sentence says | What it means | What to do |
|---|---|---|
| *X cannot read Y* | the sign-in X has no Full Access on mailbox Y | grant Full Access on Y to X in the Exchange admin center, then Check mailbox on the mailbox |
| *There is no mailbox at Y* | Y is a group, a distribution list, a typo, or deleted | use a mailbox's own address, or create the mailbox |
| *Y is a user account. Connect it as its own sign-in, or grant X Full Access and Send As on it* | Y is a person's mailbox, owned in Odoo by somebody signed in as X | the simple way: have somebody sign in to Odoo as Y and make them the owner. The other: both rights on Y for X |
| *Y is an alias on another mailbox* | Y is an extra address on some mailbox, not one of its own | configure that mailbox's primary address |
| *X has no send-as address for Y* (Google) | the Workspace account X may not put Y in From | sign in as Y, or add Y under Send mail as in X's Gmail settings |
| *Y is a send-as alias of X that Google has not verified yet* | the alias is pending | finish the verification mail in Gmail settings |
| *X may not send as Y: the server refused the sender* (SMTP) | the login may not use Y as MAIL FROM | ask the hoster to allow Y for that login, or use the login's own address |

Send As on Microsoft 365 and SMTP is only ever proven by a send: a mailbox
whose Setup tab reads **2. Check: Not checked yet** is not a fault, it is
waiting for the first mail. **Check mailbox** on that step is the way to make
that happen now.

### "Mailbox not connected" warning, or the banner will not go away

**Cause:** The user has not connected their account yet.

**Solution:**
1. Go to **My Preferences → Mail Pro** tab
2. Click **Connect mailbox** and complete the sign-in

On IMAP/SMTP there is nothing for the user to press: an administrator enters
the server, login and password on the account. The banner is not shown at all
on an IMAP/SMTP database.

### "Connect this Odoo instance to Pantalytics"

**Cause:** The instance is not connected to a Pantalytics account, or its
connection lapsed. Incoming sync and new mailbox connections need one; sending
does not.

**Solution:** An administrator connects it under **Settings → Mail Pro**, see
[Connect to Pantalytics](getting-started/connect-pantalytics.md). The message
names the state it is in (waiting for approval, key refused, replaced) and
what to press. Existing accounts can still reconnect meanwhile.

### Email stuck in outbox

**Cause:** The credentials expired or were revoked.

**Solution:**
- Microsoft 365 and Google Workspace: disconnect and reconnect the account
- IMAP/SMTP: open the account, re-enter the password, press **Test Connection**

If the mail failed rather than waited, it carries its own reason. Open it under
**Settings → Technical → Email → Emails** and read **Failure Reason**.

### Gmail: "Request had insufficient authentication scopes"

**Cause:** The account was authorized before a scope was added, so its token
does not carry it.

**Solution:** Disconnect and reconnect the account. Adding a scope in Google
Cloud does not change tokens that were already issued.

### IMAP/SMTP: mail sends but never appears in Sent

**Cause:** The server names its Sent folder something the `\Sent` special-use
flag does not point at.

**Solution:** Set **Sent Folder** on the account. Delivery already succeeded, so
this only affects the copy in your own mail client.

## Sync Issues

### Emails not syncing

**Checklist:**
1. If the missing mail is not a reply, the mailbox's **Sync level** includes new email (the third or fourth option)
2. Mailbox **Owner** is set
3. On Microsoft 365: the owner has connected; on Gmail and IMAP/SMTP: the address has its own account with **Test Connection** green
4. **Notification mailbox** exists (required for incoming sync)
5. **Internal domains** are configured — nothing syncs until that list has an entry
6. The sender is not on the **block list**, and the mail is not between your own domains

### "This mailbox is not being read"

**Cause:** The mailbox has not completed a sync run in fifteen minutes. Its
credentials are fine and nothing failed; the sync is simply not happening. The
usual reason is that Odoo has deactivated the scheduled action, which it does
when a job keeps running out of time.

**Solution:** Press **Try again** on the mailbox. If that works, the mailbox
itself is healthy and the schedule is the problem: re-enable **Mail Pro: Fetch
Incoming Email** under Settings → Technical → Scheduled Actions.

The mailbox form shows **Last checked** under the address, which is the run
rather than the newest mail read. A mailbox nobody writes to has an old
**Newest Mail Read** and a fresh **Last checked**, and that is healthy.

### "0 mailbox(es)" in logs

**Cause:** Mailbox configuration incomplete.

**Solution:** No mailbox has usable credentials. On Microsoft 365 the owner
connects their account; on Gmail and IMAP/SMTP the address has its own account
and needs no owner. The mailbox's **Sync level** decides how much is read.

### Reply threading not working

**Cause:** Usually the original email was never synced, so there was nothing to
thread onto.

Mail Pro tries four rules, strongest first, and stops at the first confident
answer:

1. **Odoo's own headers** — mail we sent carries the record it came from
2. **The `References` chain** — the standard email threading headers
3. **The provider's thread id** — `conversationId` on Microsoft, `threadId` on
   Gmail, the root of the References chain on IMAP; all scoped to one mailbox
4. **Subject and participants** — only ever a suggestion, never acted on alone

**Solution:** Open the mail's row under **Settings → Technical → Email → Mail
Pro → Mail Routing**. It records which rule placed it and every candidate it
rejected. To thread onto older conversations, set a **Import from** date on the
mailbox that predates them.

### Duplicate emails created

**Cause:** Email synced before deduplication data was stored.

**Solution:** The system uses Message-ID headers to prevent duplicates. If duplicates occur, check that the original message is indexed under the Message-ID the provider sent it with (developer mode → model `pan.mail.message.ref`, one row per Message-ID a message is known under).

## Authentication Issues

### OAuth callback error

**Possible causes:**
1. Redirect URI mismatch in the app registration or OAuth client
2. Client secret expired
3. Permissions or scopes not granted

**Solution:**
1. Verify the redirect URI matches character for character. Microsoft 365 uses
   `https://your-domain.com/microsoft_oauth/callback`, Google
   `https://your-domain.com/google_oauth/callback`. The provider form in Odoo
   shows the exact URL to paste
2. Check the client secret's expiry in the Azure Portal or Google Cloud Console
3. Microsoft 365: grant admin consent for the API permissions. Google: check
   the scopes on the consent screen

### Token refresh failing

**Cause:** The refresh token expired (Microsoft: 90 days of inactivity) or the
user revoked access.

**Solution:** The user reconnects their account under **My Preferences → Mail Pro**.

Google only issues a refresh token on the first consent. If an account stops
working an hour after connecting, it was authorized without one — disconnect and
connect again so the consent screen is shown afresh.

## Getting Help

If issues persist:

1. Check Odoo logs for `[Outgoing Mail]` and `[Incoming Mail]` entries, and
   `[Graph API]`, `[Gmail API]`, `[IMAP]` or `[SMTP]` for the provider's own errors
2. Verify the app registration: Azure permissions and admin consent, Google
   scopes and the enabled Gmail API, or the IMAP/SMTP servers and password
3. Contact support at support@pantalytics.com
