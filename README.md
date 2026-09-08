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
- Reply threading: our own headers, then the References chain, then the
  provider's thread id, then subject and participants as a suggestion
- Historical email sync with configurable start date
- Known partners filter: only sync email from existing contacts, or from
  anyone, creating contacts as needed
- Per-contact block list to exclude specific senders
- Route a mailbox to a team alias so new email creates a lead or a ticket
  instead of landing on the sender's contact

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

Until you connect, a banner sits above every screen with a button that goes
straight to the consent screen. It is only shown where that button would work:
an internal user, a provider that has a consent screen, and not on a staging
copy. Once connected, **Send Test Email** on **My Profile → Mail Pro** proves
the address really sends. Every mailbox form has the same button.

**Note:** until your account is connected and a Send from mailbox is set, the
email composer shows a warning banner saying so.

---

## Mailbox Configuration

Go to **Settings → Technical → Email → Mail Pro → Mailboxes** (the arrow on step 3 of the checklist)

### Mailbox Types

| Type | Description |
|------|-------------|
| **Personal** | User's own mailbox. Auto-created on connect. Only visible to owner. |
| **Shared** | Team mailbox (sales@, support@). Visible to all users. On Microsoft 365 each user sends with their own token and needs SendAs; on Gmail and IMAP/SMTP the address has credentials of its own and no owner. |

Exactly one mailbox also has **Notification Mailbox** ticked: system emails —
user invitations, password resets, activity reminders — go out from it, using
its owner's credentials. Tick it straight from the mailbox list; to move it,
untick the current one first.

### Incoming Email Sync

**Prerequisite:** one mailbox must have **Notification Mailbox** ticked (required for handling emails from external authors).

Replies to email sent from Odoo always land on the record they answer. That
needs no setting. The two switches below are about everything else.

1. Open a mailbox
2. Under **Sending**, decide about **Sync Sent Items**:
   - Off (the default) - only mail written in Odoo is logged
   - On - the Sent Items folder of your people's own mail app (Outlook, Gmail or
     another client) is read back into Odoo. Only replies to emails that are
     already in Odoo: the answer lands on the record it continues. Mail that
     starts a new conversation stays out, contact or not, because where it
     belongs is not a question Odoo can answer for you.

   Mail written in Odoo always goes out through the mailbox. There is no setting
   for it, because that is what a mailbox is.
3. Under **Receiving**, decide about **Sync Other Email**:
   - Off (the default) - only replies come in
   - On - email that starts a new conversation comes in too, and a second
     question appears: **Only from people who are already contacts**, or **From
     anyone**. "From anyone" turns every sender into a contact, newsletters and
     private email included, so pick it only for an address that exists to hear
     from strangers.
4. Optionally route to a **Team** (alias) so emails create tickets or leads
   instead of landing on the sender's contact
5. On Microsoft 365, set the **Owner** whose sign-in reads the mailbox. On
   Gmail and IMAP/SMTP the address has its own account and needs no owner
6. Optionally set **Start from** for historical email import
7. Save

**Sync behavior:**
- Internal domains must be configured in Settings → Mail Pro before any mailbox
  can sync. Add one tag per domain; Odoo suggests them from your mailboxes,
  company email and alias domains. Email between those domains is never synced.
- There is no way to switch this off, globally or per mailbox. A mail with any
  outside recipient is correspondence and is still logged, so "internal" means
  every party is one of your own domains
- Emails sync automatically every minute
- Set a sync start date to import historical emails (default: sync from now)

**Per-contact block list:**
- Go to a contact's form view → Email Sync tab
- Enable "Block Email Sync" to exclude that contact from all mailbox sync

---

## Troubleshooting

### Reply threading not working

Four rules run strongest-first and stop at the first confident answer: our own
`X-Odoo-*` headers, the `References` chain, the provider's thread id
(`conversationId`, `threadId`, or the root of the References chain on IMAP), and
finally subject plus participants, which never acts alone.

Open the mail's row under **Settings → Technical → Email → Mail Pro → Mail
Routing**: it records the rule that placed it and every candidate it rejected.
The usual cause is that the original message was never synced, so there was
nothing to thread onto — set an earlier **Start from** on the mailbox.

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

On Microsoft 365 and Google Workspace the module uses **delegated permissions
only** - the app acts on behalf of the signed-in user, never as an
administrator. IMAP/SMTP has no such concept: the account is a login and a
password, entered by an administrator.

| Aspect | Implementation |
|--------|----------------|
| Authentication | OAuth 2.0 with Microsoft Entra ID or Google; server login and password on IMAP/SMTP |
| Permissions | Delegated only, where the provider offers it (no admin access) |
| Credential storage | Encrypted at rest (Fernet) — tokens, passwords and the client secret |
| Shared mailbox | Microsoft 365: SendAs on the address. Gmail and IMAP/SMTP: its own credentials |

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
