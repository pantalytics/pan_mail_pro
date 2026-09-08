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

The page is a checklist of three steps; the arrow on each line opens the
table where it is answered:

1. **Email provider** - where your mail is hosted (Microsoft 365, Google
   Workspace or IMAP/SMTP), with the app registration or OAuth client that
   goes with it. The redirect URI to paste back into the provider's console
   is shown on that form. IMAP/SMTP has no global credential; see below.
2. **Internal domains** - your own email domains. Required: no mailbox can sync
   until this is answered. Odoo suggests them from your mailboxes, company
   email and alias domains.
3. **Mailboxes** - create the notification mailbox (one button, owned by you)
   and configure sending and incoming sync.

Below the checklist, **Users** shows who has connected a mailbox and invites
the rest. A database runs on one provider; switching means editing that row,
and the provider's console issues a new client secret for it.

You can invite users before any of this is finished. Mail Pro leaves SMTP alone
until the first mailbox exists, and once it does, invitations and password
resets wait in the queue until step 3 is done rather than being dropped.

### IMAP/SMTP mailboxes (Soverin and friends)

An IMAP mailbox has no consent screen, so its credentials are typed in once:

1. Go to **Settings → Technical → Email → Mail Pro → Email Accounts** and create an account.
2. Set **Provider** to *IMAP / SMTP* and fill in the address. Known hosters
   (Soverin) fill in their own servers; anything else is typed in.
3. Enter the IMAP and SMTP servers, the login (defaults to the address) and the
   password, then press **Test Connection** - both halves are checked, because a
   mailbox that reads but cannot send is broken.
4. Leave **Odoo User** empty for a shared address such as `info@`; set it for a
   person's own mailbox.
5. Create the mailbox under **Settings → Technical → Email → Mail Pro → Mailboxes**
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

An admin can also send everybody the invitation with **Send Mail Pro Invite**
on the Users list (Settings → Mail Pro → Users); the link in it drops each user
straight on the consent screen.

**Note:** until your account is connected and a Send from mailbox is set, the
email composer shows a warning banner saying so.

---

## Mailbox Configuration

Go to **Settings → Technical → Email → Mail Pro → Mailboxes** (the arrow on step 3 of the checklist)

### Mailbox Types

The type is not a setting. It follows from the owner:

| Type | Description |
|------|-------------|
| **Personal** | The owner's own address. Created when they connect. Only visible to the owner. |
| **Shared** | Any address that is not its owner's own, or has no owner (sales@, support@). Visible to all users. On Microsoft 365 each user sends with their own OAuth grant; on Gmail and IMAP the address has credentials of its own. |

On Microsoft 365 a shared mailbox that reads mail still names an **Owner**: the
person whose grant reads it. That does not make it theirs.

Exactly one mailbox also has **Notification Mailbox** ticked: system emails —
user invitations, password resets, activity reminders — go out from it, using
its owner's credentials. Tick it straight from the mailbox list; to move it,
untick the current one first.

### Incoming Email Sync

**Prerequisite:** one mailbox must have **Notification Mailbox** ticked (required for handling emails from external authors).

Replies to email sent from Odoo always land on the record they answer. That
needs no setting. Sending needs none either: which mailbox an email leaves from
is picked per message, under **Send From** in the composer, and it shows up in
the Sent Items of your mail app like any other mail.

1. Open a mailbox, tab **Sync Settings**
2. Pick a **Sync level**. Each one keeps strictly more than the one above it:

   | Level | What is read back |
   |---|---|
   | **Replies, in Odoo only** (default) | Only replies to mail Odoo sent |
   | **Replies, in Odoo and your mail app** | Also your own replies, read back from the Sent folder |
   | **Replies and new email, existing contacts only** | Also new conversations started by people who are already contacts |
   | **Replies and new email, everyone** | Also new conversations from strangers, who become contacts. Newsletters and private email included, so pick it only for an address that exists to hear from strangers |

   Under the choice the form shows what happens in Odoo for each situation:
   a reply from your contact, your reply from your mail app, new mail from
   your contact, new mail from your mail app, new mail from a stranger. Mail
   you start from your own mail app never enters, whatever the level: only
   your replies do.
3. From the third level on, optionally **route new conversations to a team**
   (alias) so they create tickets or leads instead of landing on the sender's
   contact
4. On the **Setup** tab: the **Type**, the **Owner** (a user with a connected
   account), and optionally **Import from** for historical email
5. Save

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
2. Verify the mailbox has usable credentials, and that **Sync Other Email** is on if the mail you are missing is not a reply
3. Verify the mailbox has usable credentials — its **Status** column says so
4. Check logs for `[Incoming Mail]` entries

### "0 mailbox(es)" in logs

No mailbox has usable credentials. The account behind each one is not
connected.

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

Once mail is flowing, three screens under **Settings → Technical → Email →
Mail Pro** tell you whether it is going where you expect:

| Screen | Question it answers |
|--------|--------------------|
| **All Communication** | Every mail, with the document it landed on |
| **Link Coverage** | How much of it lands on a document at all |
| **Mail Routing** | Which rule placed each mail, and what it rejected |

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

41 test files cover five areas: the provider contract, each provider's
wire behaviour, the incoming pipeline (fetch → filter → match → post),
sending, threading, the composer and onboarding, and the migration scripts.
See ARCHITECTURE.md §12.

---

## License

Mail Pro is source-available under the **[Elastic License 2.0](LICENSE)**, the
same licence as Odoo MCP Pro. In short: read the code, run it, modify it for
your own use. You may not offer it to third parties as a hosted or managed
service, and you may not remove or circumvent its licensing notices.

Releases up to and including `v19.0.7.13.1` were published under LGPL-3 and
stay under LGPL-3. The relicence applies from `19.0.7.14.0` onwards.

Mail Pro is sold direct, not through the Odoo Apps store, which requires
`OPL-1` for paid apps. See ARCHITECTURE.md §9.16 for why.

Commercial terms and support: <support@pantalytics.com>.
