# Mail Pro

Complete Microsoft 365, Google Workspace and IMAP/SMTP email integration for
Odoo - send and receive with full control.

**[Full documentation](https://pantalytics.gitbook.io/pantalytics-docs/)**

## Features

**Outgoing Email:**
- Send From dropdown in email composer
- Personal, shared, and notification mailbox support
- Auto-create personal mailbox when you connect your account
- Default mailbox per user
- One sender per mail, chosen once: a mail that cannot be sent from the mailbox
  you picked fails and says why, rather than leaving from a different address

**Providers:**
- Microsoft 365 (Graph API, OAuth 2.0)
- Google Workspace (Gmail API, OAuth 2.0)
- Any IMAP/SMTP mailbox - Soverin, Fastmail, your own server - with a server,
  a login and a password

**Incoming Email:**
- Automatic sync from every configured mailbox (1 min interval)
- 2-way sync: Inbox and Sent Items
- Reply threading via In-Reply-To headers + Microsoft conversationId fallback
- Historical email sync with configurable start date
- Known partners filter: only sync emails from existing contacts
- "All" sync mode with per-contact routing rules
- Per-contact block list to exclude specific senders
- New emails create CRM Leads with activity for mailbox owner
- Triage queue for mail that could not be filed anywhere, with an optional
  AI suggestion (off by default, your own API key, envelope only)

**Security:**
- OAuth 2.0 with delegated permissions only (least privilege)
- Token encryption at rest

---

## Installation

### As Git Submodule (Odoo.sh)

1. In Odoo.sh, go to **Settings → Submodules**
2. Click **Add submodule**
3. Enter: `git@github.com:pantalytics/pan_mail_pro.git`
4. Copy the **Public Key** and add it as Deploy Key in GitHub

```bash
# Local: add submodule
git submodule add git@github.com:pantalytics/pan_mail_pro.git addons/pan_mail_pro
git commit -m "Add pan_mail_pro submodule"
git push
```

---

## Setup

After installing the module, go to **Settings** → scroll to **Mail Pro**.

The page walks through setup in six steps, and the first one decides the rest:

1. **Email provider** - pick where your mail is hosted (Microsoft 365, Google
   Workspace or IMAP/SMTP) from the searchable dropdown. Only that provider's
   steps are shown from here on.
2. **Credentials** - copy them from your Azure app registration or your Google
   Cloud OAuth client. The redirect URI to paste back into the provider's
   console is shown here. IMAP/SMTP has no global credential; see below.
3. **Connect your account** - sign in and grant consent.
4. **Internal domains** - your own email domains. Required: no mailbox can sync
   until this is answered. Odoo suggests them from your mailboxes, company
   email and alias domains.
5. **Mailboxes** - create the notification mailbox (one button, owned by you)
   and configure sending and incoming sync.
6. **Your team** - invite colleagues to connect their own mailbox.

Each provider keeps its own credentials, so switching the dropdown loses
nothing and one database can serve mailboxes on several providers.

You can invite users before any of this is finished. Mail Pro leaves SMTP alone
until the first mailbox exists, and once it does, invitations and password
resets wait in the queue until step 5 is done rather than being dropped.

### IMAP/SMTP mailboxes (Soverin and friends)

An IMAP mailbox has no consent screen, so its credentials are typed in once:

1. Go to **Settings → Technical → Email → Email Accounts** and create an account.
2. Set **Provider** to *IMAP / SMTP* and fill in the address. Known hosters
   (Soverin) fill in their own servers; anything else is typed in.
3. Enter the IMAP and SMTP servers, the login (defaults to the address) and the
   password, then press **Test Connection** - both halves are checked, because a
   mailbox that reads but cannot send is broken.
4. Leave **Odoo User** empty for a shared address such as `info@`; set it for a
   person's own mailbox.
5. Create the mailbox under **Settings → Technical → Email → Mailboxes**
   with the same address and provider *IMAP / SMTP*.

The Sent folder is detected from the server (`\Sent`), and can be overridden on
the account when a server names it something unusual. Mail sent from Odoo is
filed there, so it shows up in your own mail client too.

---

## User Setup

### Connect your mailbox

1. Go to **My Profile** → **Preferences** → **Mail Pro**
2. Click **Connect Mailbox**
3. Sign in and grant permissions
4. A personal mailbox is created for the address you signed in with, and set as
   your **Send from**

An admin can also send everybody the invitation from **Settings → Mail Pro →
Your Team**; the link in it drops each user straight on the consent screen.

**Note:** until your account is connected and a Send from mailbox is set, the
email composer shows a warning banner saying so.

---

## Mailbox Configuration

Go to **Settings** → **Mail Pro** → **Manage Mailbox List**

### Mailbox Types

| Type | Description |
|------|-------------|
| **Personal** | User's own mailbox. Auto-created on connect. Only visible to owner. |
| **Shared** | Team mailbox (sales@, support@). Visible to all users. Each user sends with own OAuth. |

Exactly one mailbox also has **Notification Mailbox** ticked: system emails —
user invitations, password resets, activity reminders — go out from it, using
its owner's credentials.

### Incoming Email Sync

**Prerequisite:** one mailbox must have **Notification Mailbox** ticked (required for handling emails from external authors).

1. Open a mailbox
2. Choose **Incoming Mail**:
   - **Send only** - nothing is imported
   - **Send and receive, from existing contacts** - 2-way sync, known contacts only
   - **Send and receive, from anyone** - 2-way sync, new senders become contacts
3. Optionally route to a **Team** (alias) so emails create tickets or leads
   instead of landing on the sender's contact
4. Set the **Owner** (a user with a connected account)
5. Optionally set **Import From** for historical email import
6. Save

**Sync behavior:**
- Internal domains must be configured in Settings → Mail Pro before any mailbox
  can sync. Add one tag per domain; Odoo suggests them from your mailboxes,
  company email and alias domains. Email between those domains is never synced.
- There is no way to switch this off, globally or per mailbox. A mail with any
  outside recipient is correspondence and is still logged, so "internal" means
  every party is one of your own domains
- Internal users (employees with Odoo accounts) are always excluded
- Emails sync automatically every minute
- Set a sync start date to import historical emails (default: sync from now)

**Per-contact block list:**
- Go to a contact's form view → Email Sync tab
- Enable "Block Email Sync" to exclude that contact from all mailbox sync

---

## Troubleshooting

### Reply threading not working

Threading uses two methods:
1. **In-Reply-To header** - Standard email threading (works for Inbox)
2. **Microsoft conversationId** - Fallback when headers unavailable (works for Sent Items)

Check logs for "Threading reply to" entries. If replies go to the wrong record, ensure the original email was synced first (conversationId must be stored).

### Emails not syncing

1. Check **Settings** → **Technical** → **Scheduled Actions** → "Mail Pro: Fetch Incoming Mail"
2. Verify the mailbox's **Incoming Mail** is not "Send only"
3. Verify the mailbox has usable credentials — its **Status** column says so
4. Check logs for `[Incoming Mail]` entries

### "0 mailbox(es)" in logs

No mailbox both syncs and has usable credentials: either Incoming Mail is still
"Send only", or the account behind it is not connected.

### An email failed instead of being sent from another address

That is deliberate. If the chosen mailbox cannot send, you get an error naming
what to fix, and the mail waits in **Settings → Technical → Email → Emails** with
the same reason on it — rather than going out from `notifications@` and looking
like it worked. Other emails sent at the same time are unaffected.

### Permission denied when sending

User lacks SendAs permission on the mailbox in Microsoft 365. Configure this in Exchange Admin Center.

---

## Security

This module uses **Delegated Permissions only** - the app acts on behalf of the signed-in user, not as an administrator.

| Aspect | Implementation |
|--------|----------------|
| Authentication | OAuth 2.0 with Microsoft Entra ID |
| Permissions | Delegated only (no admin access) |
| Token storage | Encrypted at rest (Fernet) |
| Shared mailbox | User needs M365 SendAs permission |

See [ARCHITECTURE.md](ARCHITECTURE.md) for technical details.

---

## Where mail lands

Once mail is flowing, four screens tell you whether it is going where you
expect:

| Screen | Question it answers |
|--------|--------------------|
| **Communication → All Communication** | Every mail, with the document it landed on |
| **Communication → Link Coverage** | How much of it lands on a document at all |
| **Communication → Triage** | What reached Odoo but was filed nowhere |
| **Settings → Technical → Email → Mail Routing** | Which rule placed each mail, and what it rejected |

The Mail Routing log flags two cases for review: mail that fell back to a
contact's chatter (delivered, but nobody is looking there), and mail that
created a new record *while* other candidates existed (possibly a duplicate
ticket for a conversation already running). Threaded mail never flags — a review
queue that cries wolf gets ignored.

---

## Development

| Document | Contents |
|----------|----------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | The design: models, seams, flows, and why. Single source of truth |
| [CLAUDE.md](CLAUDE.md) | Workflow: environments, commands, CI, Odoo traps |
| [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md) | UI conventions for the settings and mailbox screens |
| [TESTPLAN.md](TESTPLAN.md) | Manual test plan for what CI cannot reach |

### Running Tests

```bash
cd .local
docker-compose stop odoo
docker-compose run --rm odoo python -m odoo -c /etc/odoo/odoo.conf \
  -d test_db -u pan_mail_pro --test-enable --test-tags=pan_mail_pro --stop-after-init
docker-compose start odoo
```

28 test files cover four areas: the provider and AI contracts, each provider's
wire behaviour, the incoming pipeline (fetch → filter → match → post), and
sending, threading, the composer and onboarding. See ARCHITECTURE.md §12.
