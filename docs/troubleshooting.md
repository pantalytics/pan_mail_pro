# Troubleshooting

## Sending Issues

### "Permission denied when sending"

**Cause:** User lacks SendAs permission on the mailbox in Microsoft 365.

**Solution:**
1. Go to [Exchange Admin Center](https://admin.exchange.microsoft.com)
2. Navigate to **Recipients → Mailboxes**
3. Select the shared mailbox
4. **Delegation → Send As** → Add the user

### "Microsoft account not connected" warning

**Cause:** User hasn't connected their Microsoft 365 account.

**Solution:**
1. Go to **My Profile → Mail Pro** tab
2. Click **Connect Microsoft 365**
3. Complete the OAuth flow

### Email stuck in outbox

**Cause:** OAuth token expired or revoked.

**Solution:**
1. Disconnect Microsoft account
2. Reconnect and re-authorize

## Sync Issues

### Emails not syncing

**Checklist:**
1. Mailbox has **Sync Mode** set (not "Send only")
2. Mailbox **Owner** is set
3. Owner has **Microsoft connected**
4. **Notification mailbox** exists (required for incoming sync)

### "0 mailbox(es)" in logs

**Cause:** Mailbox configuration incomplete.

**Solution:** Ensure Sync Mode and Owner are both configured.

### Reply threading not working

**Cause:** Original email wasn't synced, so conversationId is missing.

Threading uses two methods:
1. **In-Reply-To header** - Standard email threading
2. **Microsoft conversationId** - Fallback when headers unavailable

**Solution:** Ensure historical emails are synced by setting a Sync Start Date before the original conversation.

### Duplicate emails created

**Cause:** Email synced before deduplication data was stored.

**Solution:** The system uses Message-ID headers to prevent duplicates. If duplicates occur, check that the original email has `x_microsoft_message_id` stored.

## Authentication Issues

### OAuth callback error

**Possible causes:**
1. Redirect URI mismatch in Azure app registration
2. Client secret expired
3. Permissions not granted

**Solution:**
1. Verify redirect URI: `https://your-domain.com/microsoft_oauth/callback`
2. Check client secret expiration in Azure Portal
3. Ensure admin consent is granted for API permissions

### Token refresh failing

**Cause:** Refresh token expired (90 days of inactivity) or user revoked access.

**Solution:** User needs to reconnect their Microsoft account.

## Getting Help

If issues persist:

1. Check Odoo logs for `[Graph API]` and `[Incoming Mail]` entries
2. Verify Azure app permissions and admin consent
3. Contact support at support@pantalytics.com
