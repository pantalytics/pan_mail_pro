# Mail Pro

Complete Microsoft 365, Google Workspace and IMAP/SMTP email integration for
Odoo - send and receive with full control.



https://github.com/user-attachments/assets/91bb494e-696d-4ede-85b5-4ac5cc826e5c



**[Full documentation](https://odoo.pantalytics.com/knowledge/article/116)**

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
- Replies in a conversation that is already in Odoo always come in, on the
  record the conversation is linked to, with no setting
- One **sync level** per mailbox, each keeping strictly more than the one
  above: replies only; also your own replies from your mail app; also new
  conversations from existing contacts; also new conversations from new
  contacts, whom Mail Pro adds as contacts
- Reply threading: our own headers, then the References chain, then the
  provider's thread id, then subject and participants as a suggestion
- Historical email sync with configurable start date
- Per-contact block list to exclude specific senders
- Route a mailbox to a team alias so new email creates a lead or a ticket
  instead of landing on the sender's contact

**Security:**
- OAuth 2.0 with delegated permissions only (least privilege)
- Token encryption at rest

---

## Installation

Create a Pantalytics account at
[mcp.pantalytics.com/admin/mail-pro](https://mcp.pantalytics.com/admin/mail-pro)
(free up to 100 mails a day per Odoo instance), then add the module to your
Odoo from the branch of your Odoo series: `18.0`, `19.0` or `20.0`, Community
or Enterprise. The examples below say `19.0`; use yours. The full guide per host is
[docs/getting-started/installation.md](docs/getting-started/installation.md).

**Odoo.sh**: open a branch → **Submodule** → **Run on Odoo.sh** → `git@github.com:pantalytics/pan_mail_pro.git`,
branch `19.0`, keep the suggested path. Or from your repository:

```bash
git submodule add -b 19.0 https://github.com/pantalytics/pan_mail_pro pantalytics/pan_mail_pro
git commit -m "Add pan_mail_pro" && git push
```

**Cloudpepper**: your instance → **Addons** → **Add Git repository** →
`https://github.com/pantalytics/pan_mail_pro`, branch `19.0`, **All addons
(recommended)** → **Add module**. Then **Settings** on that row: switch on
**Install Python requirements on update** and **Auto upgrade module(s) on
update**. Step by step in the [guide](docs/getting-started/installation.md#cloudpepper).

**Your own server or Docker**: `git clone -b 19.0
https://github.com/pantalytics/pan_mail_pro` into a folder on `addons_path`,
`pip install cryptography`, restart Odoo.

Then **Apps → Update Apps List → Mail Pro → Install** on every host.

---

## Setup

After installing the module, go to **Settings** → scroll to **Mail Pro** and
press **Connect to Pantalytics** (see
[docs/getting-started/connect-pantalytics.md](docs/getting-started/connect-pantalytics.md)).

The page is a checklist of five steps. Step 1 is that connection, and its
arrow opens the account page (who connected, plan, usage and billing,
disconnect); the arrow on each of the other lines opens the table where it
is answered:

2. **Email provider** - where your mail is hosted (Microsoft 365, Google
   Workspace or IMAP/SMTP). Microsoft 365 signs in with the Pantalytics Mail
   Pro app: open the step and press **Sign in with Microsoft**. To use
   Google Workspace or IMAP/SMTP, change the Provider on that form. Google's
   OAuth client goes on the same provider form, which shows the redirect URI to paste back into
   the provider's console. IMAP/SMTP has no global credential; see below.
3. **Internal domains** - your own email domains. Required: no mailbox can sync
   until this is answered. When an admin connects their own mailbox, the
   domain of that address is filled in for you (never a public one like
   gmail.com). An empty list offers the domains it can read off the
   database (never the Odoo host) beside New. Add a second domain or change
   it on the list.
4. **Mailboxes** - the addresses Mail Pro sends and receives on. Done once
   one of them is signed in and working.
5. **Notification mailbox** - the mailbox Odoo sends its own email from,
   like password resets. If none is ticked yet, the admin's own mailbox takes
   the job once it is connected. A shared one is best; pick another one from
   the mailbox list whenever you like.

Who has connected a mailbox, and the invite for the rest, are on the user
list. A database runs on one provider; switching means editing that row,
and the provider's console issues a new client secret for it.

You can invite users before any of this is finished. Mail Pro leaves SMTP alone
until the first mailbox exists, and once it does, invitations and password
resets wait in the queue until step 5 is done rather than being dropped.

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

1. Go to **My Preferences** → **Mail Pro**
2. Click **Connect mailbox**
3. Sign in and grant permissions
4. A personal mailbox is created for the address you signed in with, and set as
   your **Default mailbox**

An admin can also send everybody the invitation with **Send Mail Pro Invite**
on the Users list (Settings → Users & Companies → Users); the link in it drops each user
straight on the consent screen.

Until you connect, a banner sits above every screen with a button that goes
straight to the consent screen. It is only shown where that button would work:
an internal user, a provider that has a consent screen, and not on a staging
copy. Once connected there is nothing left to do: the address sends.

**Note:** until your account is connected and a default mailbox is set, the
email composer shows a warning banner saying so.

---

## Mailbox Configuration

Go to **Settings → Technical → Email → Mail Pro → Mailboxes** (the arrow on step 4 of the checklist)

### Mailbox Types

The **Type** column shows what Microsoft 365 or Google says the mailbox is:
Personal or Shared. When the provider has not said, it follows the owner. It
is empty for an IMAP login or an address with no mailbox. It is not a setting.

Who may send from a mailbox is a separate rule, and it follows from the owner:

| Type | Description |
|------|-------------|
| **Personal** | The owner's own address. Created when they connect. Only visible to the owner. |
| **Shared** | Any address that is not its owner's own, or has no owner (sales@, support@). Visible to all users. On Microsoft 365 each user sends with their own OAuth grant; on Gmail and IMAP the address has credentials of its own. |

On Microsoft 365 a shared mailbox that reads mail still names an **Owner**: the
person whose grant reads it. That does not make it theirs.

Exactly one mailbox also has **Notification Mailbox** ticked: system emails —
user invitations, password resets, activity reminders — go out from it, using
its owner's credentials. Tick it straight from the mailbox list; ticking
another mailbox moves it there. The last one cannot be unticked, archived or
deleted.

### Incoming Email Sync

**Prerequisite:** one mailbox must have **Notification Mailbox** ticked (required for handling emails from external authors).

Replies to email sent from Odoo always land on the record they answer. That
needs no setting. Sending needs none either: which mailbox an email leaves from
is picked per message, under **Send From** in the composer, and it shows up in
the Sent Items of your mail app like any other mail.

1. Open a mailbox, tab **Sync Settings**
2. Move the **Sync level** slider. Each stop keeps strictly more than the one before it, and the grid under the slider shows which emails Mail Pro brings into Odoo:

   | Level | What comes into Odoo |
   |---|---|
   | **Replies** (default) | Only replies in conversations that are already in Odoo |
   | **Received and sent replies** | Also your own replies from your mail app |
   | **Replies and existing contacts** | Also new conversations from people who are already contacts |
   | **Replies, existing and new contacts** | Also new conversations from new contacts, whom Mail Pro adds as contacts. Newsletters and private email included, so pick it only for an address that exists to hear from new contacts |

   A new conversation you start in your mail app never comes into Odoo,
   whatever the level. Only your replies do.
3. From the third level on, optionally **route new conversations to a team**
   (alias) so they create tickets or leads instead of landing on the sender's
   contact
4. On the **Setup** tab: the **Owner** (a user with a connected
   account), and optionally **Import from** for historical email
5. Save

**Sync behavior:**
- Internal domains must be configured in Settings → Mail Pro before any mailbox
  can sync. Add one row per domain; an empty list suggests them from your
  mailboxes, colleagues, company email and alias domains, never the Odoo
  host. Email between those domains is never synced.
- There is no way to switch this off, globally or per mailbox. A mail with any
  outside recipient is correspondence and is still logged, so "internal" means
  every party is one of your own domains
- The one exception is by hand: the owner of a personal mailbox can open it
  live in the Inbox and press **Add to Odoo** on one internal mail. It then
  lands where any mail lands, usually on the sender's contact, and is readable
  by whoever can read that record. The sync itself never does this
- Emails sync automatically every minute
- Set a sync start date to import historical emails (default: sync from now)

**Per-contact block list:**
- Go to a contact's form view → **Mail Pro** tab
- Tick **Block Sync** to exclude that contact from all mailbox sync

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
nothing to thread onto — set an earlier **Import from** on the mailbox.

### Emails not syncing

1. Check **Settings** → **Technical** → **Scheduled Actions** → "Mail Pro: Fetch Incoming Mail"
2. Verify the mailbox's **Sync level** includes new email if the mail you are missing is not a reply
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

The suite covers the provider contract, each provider's wire behaviour, the
incoming pipeline (fetch → filter → match → post), sending and threading, the
composer and the screens, the Inbox, the routes, and the migration scripts.
ARCHITECTURE.md §12 lists every file.

---

## License

Mail Pro is source-available under the **[Elastic License 2.0](LICENSE)**, the
same licence as Odoo MCP Pro. In short: read the code, run it, modify it for
your own use. You may not offer it to third parties as a hosted or managed
service, and you may not remove or circumvent its licensing notices.

Releases up to and including `v19.0.7.13.1` were published under LGPL-3 and
stay under LGPL-3. The relicence applies from `19.0.7.14.0` onwards.

Mail Pro is listed free in the Odoo Apps store; the paid plans are a
Pantalytics subscription, bought from inside the app. A paid store listing
would require `OPL-1`. See ARCHITECTURE.md §9.16 for why.

Commercial terms and support: <support@pantalytics.com>.
