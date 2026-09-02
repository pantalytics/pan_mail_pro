# IMAP/SMTP Setup

Use this when your mail is not at Microsoft 365 or Google Workspace — Soverin,
Fastmail, your own mail server, or any host that offers IMAP and SMTP.

An IMAP mailbox has no consent screen, so there is no app registration and no
global credential to configure. Each mailbox is given a server, a login and a
password once.

## Step 1: Create the account

1. Go to **Settings → Technical → Email → Email Accounts**
2. Create a new account
3. Set **Provider** to *IMAP / SMTP*
4. Fill in the email address

Known hosters fill in their own servers automatically — typing a `@soverin.net`
address sets the Soverin servers and ports for you. Anything else is typed in.

## Step 2: Enter the servers

| Field | Typical value |
|-------|---------------|
| IMAP Server | `imap.yourhost.com` |
| IMAP Port | `993` |
| IMAP Security | SSL/TLS |
| SMTP Server | `smtp.yourhost.com` |
| SMTP Port | `465` |
| SMTP Security | SSL/TLS |
| Login | defaults to the email address |
| Password | the mailbox password, or an app password |

If your host requires an **app-specific password** (most do when two-factor
authentication is on), generate one there and use it here. A normal account
password will simply be refused.

## Step 3: Test the connection

Press **Test Connection**.

Both halves are checked — reading and sending — because a mailbox that can read
but not send is broken in a way you would otherwise only discover the first time
someone tries to reply to a customer.

## Step 4: Decide whether it has an owner

**Leave Odoo User empty** for a shared address such as `info@` or `sales@`. The
address is then its own account, visible to everyone, sending with its own
credentials.

**Set Odoo User** for a person's own mailbox. It then behaves as a personal
mailbox: only that user sees it in the composer.

## Step 5: Create the mailbox

1. Go to **Settings → Technical → Email → Mailboxes**
2. Create a mailbox with the same address
3. Set **Provider** to *IMAP / SMTP*
4. Configure sending and incoming sync as described in
   [Mailboxes](../configuration/mailboxes.md)

Mailboxes on all three providers live in that one list.

## The Sent folder

Plain SMTP does not file a copy of what it sends, so Mail Pro appends the sent
copy to your Sent folder itself. That is why mail sent from Odoo also shows up
in your own mail client.

The folder is detected from the server's `\Sent` special-use flag. When a server
names it something unusual, override it with **Sent Folder** on the account.

Filing the copy is best-effort: if the append fails the mail has still been
delivered, so it is logged rather than treated as a send failure.

## Known limitations

**Threading.** IMAP gives no thread identifier, so Mail Pro threads on the
`References` chain of the message itself. This is standard RFC 5322 behaviour
and works across mail clients — it just means that a correspondent whose client
strips those headers starts a new thread.

**Sync granularity.** The IMAP `SEARCH SINCE` command only understands dates,
not times, and it uses the server's clock. Mail Pro therefore asks for a wider
window than it needs and narrows it in Odoo. You may see the sync look at
messages it then skips; that is expected.

## Next Steps

Proceed to [Mailboxes](../configuration/mailboxes.md) to configure sending and
incoming sync.
