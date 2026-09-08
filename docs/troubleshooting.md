# Troubleshooting

## Sending Issues

### "Permission denied when sending" (Microsoft 365)

**Cause:** User lacks SendAs permission on the mailbox in Microsoft 365.

**Solution:**
1. Go to [Exchange Admin Center](https://admin.exchange.microsoft.com)
2. Navigate to **Recipients → Mailboxes**
3. Select the shared mailbox
4. **Delegation → Send As** → Add the user

### "Mailbox not connected" warning, or the banner will not go away

**Cause:** The user has not connected their account yet.

**Solution:**
1. Go to **My Profile → Mail Pro** tab
2. Click **Connect Mailbox** and complete the sign-in
3. Press **Send Test Email** to confirm

On IMAP/SMTP there is nothing for the user to press: an administrator enters
the server, login and password on the account. The banner is not shown at all
on an IMAP/SMTP database.

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
1. If the missing mail is not a reply, **Sync Other Email** is on
2. Mailbox **Owner** is set
3. On Microsoft 365: the owner has connected; on Gmail and IMAP/SMTP: the address has its own account with **Test Connection** green
4. **Notification mailbox** exists (required for incoming sync)
5. **Internal domains** are configured — nothing syncs until that list has an entry
6. The sender is not on the **block list**, and the mail is not between your own domains

### "0 mailbox(es)" in logs

**Cause:** Mailbox configuration incomplete.

**Solution:** Switch on **Sync Other Email** or **Sync Sent Items**. On
Microsoft 365 also set the Owner; on Gmail and IMAP/SMTP the address has its own
account and needs no owner.

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
rejected. To thread onto older conversations, set a **Start from** date on the
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

**Solution:** The user reconnects their account under **My Profile → Mail Pro**.

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
