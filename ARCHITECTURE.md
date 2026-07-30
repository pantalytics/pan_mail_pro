# Architecture

Technical documentation for developers working on the Mail Pro module.

---

## 1. Overview

### Purpose
Complete Microsoft 365, Google Workspace and IMAP/SMTP email integration for
Odoo - send and receive emails via the Graph API, the Gmail API or the mail
protocols themselves, with proper threading and partner management.

### Provider abstraction

Everything wire-specific — how a mail is sent, how remote messages are listed
and read, which credentials to use — lives behind one contract,
`mail.provider.client`. Everything else — mailbox routing, partner matching,
threading, chatter posting — is provider-neutral and never touches a Graph or
Gmail JSON key. A mailbox names its provider with `x_provider` and dispatches
via `mailbox._get_client()`, which resolves the code through the registry in
`models/mail_provider_client.py`.

That neutrality is what made the third provider cheap. IMAP/SMTP has no OAuth,
no server-side message id and no thread id, and none of that reaches a caller:
credentials answer `account_is_connected()` instead of "has a refresh token", a
message id is the folder-scoped `folder:uidvalidity:uid` triple, and the thread
key is the root of the References chain.

There is exactly one layer: the client *is* the contract implementation, and it
lives in `models/providers/<vendor>/`. Credentials are a `pan.mail.account`, and
the client is what decides which account applies — `resolve_sending_account()`
and `resolve_receiving_account()` — because that is where providers genuinely
diverge.

Model names still read `microsoft.*` and fields `x_microsoft_*`. That is
deliberate: the rename to provider-neutral names is a single mechanical phase
(Phase 4) done last, so historical data — notably `x_microsoft_message_id` on
`mail.message`, which reply threading depends on — migrates once, cleanly.

### Key Models

| Model | Purpose |
|-------|---------|
| `x_microsoft.mailbox` | Mailbox configuration (email, type, sync, routing, `x_provider`) |
| `mail.provider.client` | The contract (abstract): resolve credentials, send, fetch, normalize |
| `microsoft.graph.client` | Microsoft 365 implementation — all Graph API calls |
| `google.gmail.client` | Google Workspace implementation — all Gmail API calls |
| `imap.smtp.client` | IMAP/SMTP implementation — imaplib + smtplib, no OAuth |
| `pan.mail.account` | Credentials for one email address on one provider (nullable `user_id`) |
| `microsoft.incoming.mail.processor` | Incoming email sync (cron), provider-neutral |
| `mail.mail` | Outgoing email override (routes through the mailbox's provider) |
| `mail.message` | Stores Microsoft message IDs for reply threading |
| `mail.compose.message` | Composer "Send From" dropdown + setup warning |
| `res.users` | OAuth token fields — now proxies onto the user's `pan.mail.account` |
| `res.partner` | Contact block list field (`x_email_sync_blocked`) |
| `res.config.settings` | Module settings (client_id, secret, tenant) |

`pan.mail.account` holds the credentials that used to live on `res.users`. An
account with a `user_id` is a person's own connection; an account with none is a
service account — how a Gmail shared mailbox works, where the address is a real
Workspace account with no Odoo user behind it. The `res.users.x_microsoft_*`
fields are unstored compute/inverse proxies onto the user's Microsoft account, so
every existing caller keeps working while the credentials live in one place.

### Module Structure

```
pan_mail_pro/
├── models/
│   ├── mail_provider_client.py    # mail.provider.client — the contract + provider registry
│   ├── mail_mail.py               # Outgoing override → mailbox._get_client().send_message()
│   ├── mail_message.py            # Microsoft message ID storage
│   ├── mail_compose_message.py    # Composer integration + setup warning
│   ├── microsoft_mailbox.py       # Mailbox configuration + routing + x_provider dispatch
│   ├── pan_mail_account.py        # Per-address credentials (pan.mail.account)
│   ├── pan_mail_fetcher.py        # Incoming email processor (provider-neutral)
│   ├── providers/                 # The only place provider payloads are understood
│   │   ├── microsoft/
│   │   │   └── graph_client.py    # microsoft.graph.client — Graph API + normalization
│   │   ├── google/
│   │   │   └── gmail_client.py    # google.gmail.client — Gmail API + normalization
│   │   ├── imap_smtp/
│   │   │   └── imap_client.py     # imap.smtp.client — IMAP/SMTP + normalization
│   │   └── mime_utils.py          # Outgoing MIME, shared by the two MIME senders
│   ├── res_users.py               # OAuth token proxies onto pan.mail.account
│   ├── res_partner.py             # Contact block list field
│   ├── res_config_settings.py     # Module settings
│   └── encryption_utils.py        # Fernet encryption
├── controllers/
│   └── main.py                    # OAuth callback handlers (Microsoft + Google)
├── wizard/
│   └── microsoft_oauth_wizard.py  # Connect Microsoft account
├── migrations/
│   └── 19.0.2.1.0/                # Copy user tokens → pan.mail.account
├── views/
├── data/
│   └── ir_cron_data.xml           # Incoming mail cron (1 min)
└── security/
```

---

## 2. Mailbox Types

### Overview

| Type | Who sees it? | Whose credentials? | Use case |
|------|--------------|--------------------|----------|
| **Personal** | Only owner | Owner's | User's own mailbox (john@company.com) |
| **Shared** | Everyone | Sender's own on Microsoft 365; the address's own on Gmail and IMAP | Team mailbox (sales@company.com) |
| **Notification** | Everyone | Owner's | System emails (notifications@company.com) |

Which credentials a mailbox runs on is asked of the provider
(`resolve_sending_account` / `resolve_receiving_account`), never assumed by the
caller: only Microsoft 365 lets one person send as another with their own token.

### Graph API Send Flow (Draft → Send)

We use a **Draft → Send** flow instead of the simpler `sendMail` endpoint:

1. **Create draft**: `POST /users/{email}/messages` → returns `internetMessageId` and `conversationId`
2. **Send draft**: `POST /users/{email}/messages/{id}/send`

**Why not use `sendMail`?**
- `sendMail` doesn't return the Microsoft message IDs
- We need `internetMessageId` to prevent duplicate imports from Sent Items sync
- We need `conversationId` for email threading

| Type | Draft Endpoint | Required Permissions |
|------|----------------|----------------------|
| **Personal** | `/users/{email}/messages` | `Mail.ReadWrite` |
| **Shared** | `/users/{email}/messages` | `Mail.ReadWrite.Shared` + SendAs in Exchange |
| **Notification** | `/users/{email}/messages` | `Mail.ReadWrite.Shared` + SendAs in Exchange |

**Note:**
- `{email}` = the mailbox email address (e.g., `team1@company.com`)
- Sent emails are stored in the mailbox's Sent Items folder

### Personal Mailbox

- Auto-created when user connects Microsoft account (if admin setting allows)
- `x_owner_user_id` field links mailbox to owner
- Only visible to owner in composer dropdown
- Owner sends with their own OAuth token

### Shared Mailbox

- Visible to all users in composer dropdown
- **Microsoft 365:** each user sends with their **own** OAuth token. User needs:
  - `Mail.ReadWrite.Shared` permission in Azure
  - "Send As" rights on the mailbox in Microsoft 365
- **Gmail and IMAP/SMTP:** the address is its own account, with its own
  credentials and no owner. Nothing is borrowed from the sender, and the mailbox
  is configured by giving that address credentials of its own
  (Settings → Technical → Email → Email Accounts).

### Notification Mailbox

- For system notifications (activity reminders, mentions to internal users)
- Uses the Owner's OAuth token (same field as personal mailboxes)
- Only one active notification mailbox allowed
- **Required for incoming sync:** When enabling incoming sync on any mailbox, a notification mailbox must exist (enforced via constraint). This handles emails triggered by external authors.

---

## 3. Sync Modes

### Overview

Each mailbox can be configured with a sync mode that determines how incoming emails are handled.

| Mode | Inbox | Sent Items | Filter | Use Case |
|------|-------|------------|--------|----------|
| **Send only** | - | - | - | Send-only mailbox |
| **Known partners** | ✓ | ✓ | Existing partners only | Safe default, no spam |
| **All** | ✓ | ✓ | Configurable per contact type | Full control with routing rules |

### Send Only (No Sync)

- Mailbox is only used for sending emails from Odoo
- No incoming emails are synchronized
- Default for new mailboxes

### Known Partners Only (Recommended)

**Decision:** Only sync emails from/to contacts that already exist as partners in Odoo.

**Filter logic differs between Inbox and Sent Items:**

**Inbox (incoming emails):**
```python
# 1. Skip if sender is from internal domain
if sender_domain in internal_domains_setting:
    skip("Internal domain")

# 2. Find partner by sender email
partner = find_partner(from_email)

# 3. Skip if sender not in Odoo
if not partner:
    skip("Unknown sender")

# 4. Skip if sender is an internal user (employee with Odoo account)
if partner.user_ids:
    skip("Internal user")

# 5. Process the email
process_email()
```

**Sent Items (outgoing emails for 2-way sync):**
```python
# 1. NO internal domain check (we sent it, we know it's valid)

# 2. Find partner by RECIPIENT email (first toRecipient)
partner = find_partner(to_email)

# 3. Skip if recipient not in Odoo
if not partner:
    skip("Unknown recipient")

# 4. Skip if recipient is an internal user (colleague with Odoo account)
if partner.user_ids:
    skip("Internal user")

# 5. Process the email (author = mailbox owner)
process_email()
```

**Why Sent Items uses different logic:**
- **No internal domain check:** The sender is always "us" (the mailbox). Checking the internal domain would skip ALL sent emails.
- **Use recipient, not sender:** We want to sync emails TO external contacts, not from ourselves.
- **Still skip internal users:** Emails to colleagues don't need to be synced (they have Odoo inbox).

**What gets synced (Inbox):**

| Scenario | Internal domain? | Partner exists? | Has user? | Result |
|----------|------------------|-----------------|-----------|--------|
| Reply from customer | - | ✓ | - | **Sync** |
| New email from existing customer | - | ✓ | - | **Sync** |
| Colleague (any @company.com) | ✓ | - | - | Skip |
| Colleague with Odoo account | - | ✓ | ✓ | Skip |
| Spam/marketing | - | - | - | Skip |
| Unknown sender | - | - | - | Skip |

**What gets synced (Sent Items):**

| Scenario | Partner exists? | Has user? | Result |
|----------|-----------------|-----------|--------|
| Email to customer | ✓ | - | **Sync** |
| Email to new lead | ✓ | - | **Sync** |
| Email to colleague | ✓ | ✓ | Skip |
| Email to unknown recipient | - | - | Skip |

**Why this approach:**
- Simple "Internal Domains" setting in Mail Pro configuration
- Explicit control over which domains are excluded
- Internal employees filtered via domain (Inbox) OR via `partner.user_ids` (both folders)
- Replies always work (partner was created when we sent to them)
- Spam/marketing naturally filtered (not in contacts)
- Simple to understand and maintain

### Email Routing Configuration

**Status:** Implemented

Each mailbox has routing settings that control how new emails (non-replies) are processed.

#### UI Fields (Progressive Disclosure)

| Field | Type | Description |
|-------|------|-------------|
| `x_incoming_sync` | Boolean toggle | Enable/disable incoming email sync |
| `x_routing_smart` | Boolean toggle | Let AI decide routing (future) |
| `x_sync_unknown_contacts` | Boolean | Also sync from senders not in Odoo |
| `x_queue_unknown_contacts` | Boolean | Queue unknown senders for review (future) |

#### Routing Priority

1. **Smart Routing (AI)** - if enabled, AI classifies email intent (coming soon)
2. **Odoo Alias** - if `mail.alias` exists for mailbox email, use its config (model + defaults)
3. **Fallback** - post to partner's chatter with warning log

#### Unknown Contact Options (when `x_sync_unknown_contacts` = True)

| Option | Behavior |
|--------|----------|
| **Create automatically** | Create partner + record |
| **Queue for review** | Hold for manual approval (future) |

#### Block List

Contacts can be individually blocked from email sync via `x_email_sync_blocked` on `res.partner`. Blocked contacts are skipped regardless of routing settings.

#### Routing Logic

```
Email arrives
    │
    ▼
Pre-filters (duplicates, Odoo-originated, internal domain)
    │
    ▼
Reply check (In-Reply-To / conversationId)
    │
    ├── Reply found → Post to existing thread
    │
    └── New email:
        │
        ├── Partner blocked? → Skip
        │
        ├── Unknown contact + x_sync_unknown_contacts = False → Skip
        │
        └── Route to model:
            ├── Smart Routing enabled → AI decides (future)
            ├── Odoo alias exists → Use alias config (model + team_id)
            └── No routing → Post to partner chatter + warning
```

**Benefits for AI integration:**
- Each conversation = 1 Lead/Ticket record
- Records linked to `partner_id` → aggregate per company
- Structured data for AI summaries per customer

### Roadmap: Unknown Contact Triage (AI-Assisted)

**Status:** Planned for v2.0

**Goal:** Allow syncing emails from unknown senders with AI-powered qualification.

**Approach:** Use existing Odoo models with ICP (Ideal Customer Profile) qualification.

**New field on `res.partner`:**
```python
x_icp_qualified = fields.Selection([
    ('pending', 'Pending Review'),      # New contact, not yet qualified
    ('qualified', 'Qualified (ICP)'),   # Matches Ideal Customer Profile
    ('not_qualified', 'Not Qualified'), # Does not match ICP, skip emails
], default='pending', string='ICP Status')
```

**Triage flow:**
```
Unknown sender email arrives
    │
    ▼
┌─────────────────────────────────────┐
│ Auto-create contact (pending)       │
│ x_icp_qualified = 'pending'         │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ AI analyzes email + company info    │
│ → Suggests: Qualified / Not         │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ User reviews & approves             │
│ Sets x_icp_qualified                │
└─────────────────────────────────────┘
    │
    ▼
Future emails from this contact:
├── qualified → Sync normally
└── not_qualified → Skip
```

**Why this approach:**
- Uses existing `res.partner` model (no new tables)
- Simple boolean-like qualification
- AI suggests, human approves (human-in-the-loop)
- Once qualified, no further triage needed
- Scales: millions of contacts, only review new ones

**AI Integration points:**
1. **Qualification suggestion:** Analyze email content, company domain, LinkedIn data
2. **Customer summary:** Aggregate all Leads per company for account overview
3. **Smart routing:** Suggest Lead vs Ticket based on content

### Pre-filters (Always Applied)

Before the sync mode filter, these checks always run:

| Check | Condition | Action |
|-------|-----------|--------|
| Odoo-originated (headers) | `X-Odoo-Model` or `X-Odoo-Mail-Id` header present | Skip |
| Odoo-originated (sent) | `internetMessageId` matches `mail.mail.x_microsoft_message_id` | Skip |
| Duplicate | `internetMessageId` already in `mail.message.message_id` | Skip |

---

## 4. Email Flows

### Outgoing Email Flow

```
User clicks "Send"
      │
      ▼
┌─────────────────────────────────────────────┐
│ mail.compose.message                         │
│ - x_microsoft_send_from_id = selected mailbox│
└─────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────┐
│ mail.mail._send()                            │
│ - Determines mailbox + user for OAuth token  │
│ - Personal/Shared: current user's token      │
│ - Notification: owner's token                │
└─────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────┐
│ microsoft.graph.client.send_email_via_graph()│
│ - POST /users/{email}/sendMail               │
│ - Fetches Message-ID from Sent Items         │
└─────────────────────────────────────────────┘
```

### Incoming Email Flow (Polling)

```
Cron job (every 1 min)
      │
      ▼
┌─────────────────────────────────────────────┐
│ fetch_messages()                             │
│ - GET /users/{email}/mailFolders/Inbox       │
│ - Filter: receivedDateTime > last_sync       │
│ - Sort: receivedDateTime asc (oldest first)  │
│ - Batch: up to 200 per folder                │
└─────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────┐
│ Filter checks                                │
│ - Skip if from internal domain               │
│ - Skip if sender not in contacts             │
│ - Skip if sender is internal user            │
└─────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────┐
│ Check In-Reply-To header                     │
│ - Find parent message by Message-ID          │
└─────────────────────────────────────────────┘
      │
      ├── Reply found ──────────────────────┐
      │                                      ▼
      │                    ┌─────────────────────────────────┐
      │                    │ Post to parent's record         │
      │                    │ (sale.order, lead, partner...)  │
      │                    └─────────────────────────────────┘
      │
      └── No reply ─────────────────────────┐
                                             ▼
                           ┌─────────────────────────────────┐
                           │ Route via alias config          │
                           │ Model.message_new(msg_dict)     │
                           │ → helpdesk.ticket, crm.lead, etc│
                           └─────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────┐
│ mail.message created                         │
└─────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────┐
│ Advance cursor                               │
│ - x_last_sync_date = min(folder cursors)     │
│ - If no messages: x_last_sync_date = now()   │
└─────────────────────────────────────────────┘
```

### Odoo Internal Mail Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  USER ACTION                                                                │
│  - Post message in chatter ("Send message")                                 │
│  - @mention someone                                                         │
│  - Activity reminder                                                        │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  mail.thread.message_post()                                                 │
│  - Creates mail.message                                                     │
│  - Calls _notify_thread()                                                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
┌───────────────────────────────────┐ ┌───────────────────────────────────────┐
│ User notification_type = 'inbox'  │ │  User notification_type = 'email'     │
│                                   │ │                                       │
│ - Odoo inbox notification         │ │  - Creates mail.mail                  │
│ - NO email sent                   │ │  - Routes to Graph API                │
└───────────────────────────────────┘ └───────────────────────────────────────┘
```

---

## 5. Design Decisions

### 5.1 Token Encryption

**Decision:** Fernet symmetric encryption with auto-generated key in database.

**Why:**
- Odoo.sh doesn't support custom environment variables
- Database is already encrypted at rest on Odoo.sh
- Zero configuration required
- Defense-in-depth against SQL injection, backup leaks

**Implementation:**
- Key stored in `ir.config_parameter`: `x_pan_outlook_pro.encryption_key`
- Auto-generated on first use
- All encryption via `models/encryption_utils.py`

### 5.2 Polling over Webhooks

**Decision:** Poll Microsoft Graph API every 1 minute instead of using webhooks.

**Why:**
- Works out-of-the-box on Odoo.sh (no public endpoint needed)
- Reuses existing OAuth token infrastructure
- 1-minute delay acceptable for email sync

### 5.3 Native message_new() for Incoming Emails

**Decision:** Use Odoo's native `message_new()` for creating records from incoming emails.

**Why:**
- This is exactly what Odoo's standard SMTP mail gateway uses
- Handles record creation + initial message posting correctly
- Triggers auto-replies (e.g., Helpdesk acknowledgment) properly
- Does NOT send duplicate follower notifications to the sender
- Proven Odoo code for threading, attachments, partner creation

**Critical insight:** Manually creating records + calling `message_post()` triggers follower notifications, which causes senders to receive duplicate emails. The `message_new()` approach avoids this entirely.

```python
# CORRECT - uses Odoo's native flow
record = self.env['helpdesk.ticket'].message_new(msg_dict, custom_values={'team_id': team.id})

# WRONG - triggers unwanted notifications
record = self.env['helpdesk.ticket'].create(vals)
record.message_post(body=body, ...)  # Sender gets notified!
```

### 5.4 Reply Threading via Microsoft Message-ID

**Decision:** After sending, store Microsoft's `internetMessageId` on `mail.message` for reply threading.

**Why:**
- Graph API `sendMail` doesn't return Message-ID
- Microsoft generates its own Message-ID (different from any we set)
- Need Microsoft's ID for correct reply threading
- Incoming replies use `In-Reply-To` header to find parent message

**Implementation:**
1. After sending via Graph API, the `internetMessageId` is returned from the draft
2. Store it on `mail.message.x_microsoft_message_id`
3. Incoming mail processor looks up parent via `In-Reply-To` header matching stored IDs

### 5.5 Incremental Cursor-Based Sync

**Decision:** Use ascending sort + incremental cursor for reliable email sync.

**Pattern:**
1. Fetch messages sorted by `receivedDateTime asc` (oldest first)
2. Process batch of up to 200 messages per folder
3. Advance `x_last_sync_date` to the `receivedDateTime` of the last fetched message
4. Next cron run continues from where the previous run stopped

**Why ascending + incremental cursor:**
- **No data loss:** Each run picks up exactly where the previous one stopped
- **Self-healing:** If a run fails, the next run retries from the same point
- **Historical import:** Processes 200 messages per cron run until caught up
- **No race conditions:** Messages arriving during processing have later timestamps

**Multi-folder cursor (Inbox + SentItems):**
- Both folders share one cursor (`x_last_sync_date`)
- After processing both, cursor advances to the **minimum** of the two folders' latest message
- This ensures no messages are skipped in the slower folder
- Duplicates from the faster folder are automatically skipped via `internetMessageId` check

**Configuration:**
- `x_sync_start_date`: User-configurable start date (default: now, always editable)
- `x_last_sync_date`: Auto-advancing cursor, updated after each successful batch
- Changing `x_sync_start_date` to an earlier date auto-resets `x_last_sync_date`
- Duplicates are automatically skipped via `internetMessageId` check

**Example: Historical import of 1000 emails:**
```
Run 1: fetch 200 oldest since sync_start_date → cursor = msg #200 datetime
Run 2: fetch 200 oldest since cursor          → cursor = msg #400 datetime
...
Run 5: fetch 200 oldest since cursor          → cursor = msg #1000 datetime
Run 6: fetch 0 messages                       → cursor = now() (caught up)
```

This is a standard pattern (incremental cursor sync) used by Odoo fetchmail, Stripe webhooks, Salesforce replication, etc.

### 5.6 Pre-create Partners

**Decision:** Find/create partner BEFORE calling `message_process()`.

**Why:**
- Odoo's auto-creation sometimes uses email subject as partner name
- Pre-creation ensures correct name from email "From" header

### 5.7 Personal Mailbox Auto-creation

**Decision:** Auto-create personal mailbox when user connects Microsoft account.

**Why:**
- Zero-friction onboarding for users
- Admin can disable via setting if not wanted
- Mailbox immediately available in composer dropdown

### 5.8 Shared Mailbox: User's Own Token

**Decision:** Each user sends from shared mailbox using their own OAuth token.

**Why:**
- Principle of least privilege
- No centralized "super user" needed
- Audit trail shows actual sender
- User needs proper M365 permissions anyway

---

### 5.9 IMAP/SMTP: what the protocols make the contract absorb

**Decision:** support plain IMAP + SMTP as a third provider (`imap`), on
stdlib `imaplib`/`smtplib`, with credentials on `pan.mail.account`.

**Why:** not every mailbox is at Microsoft or Google. A hoster such as Soverin
offers IMAP and SMTP and nothing else, and the module was already one contract
away from being able to use it.

Three protocol facts had to land somewhere, and all three land inside the client:

| Fact | Consequence |
|------|-------------|
| No OAuth | `account_is_connected()` is a provider question. For IMAP it means host + login + password; the OAuth-shaped contract methods refuse with an explanation instead of pretending. |
| UIDs are folder-scoped and invalidated by `UIDVALIDITY` | `provider_message_id` is `folder:uidvalidity:uid`. A renumbered folder is refused rather than misread — a bare UID would fetch a different message. |
| No thread id | The root of the `References` chain is the thread key, so every message in a conversation shares one handle, the way `conversationId` and `threadId` do. |

Two smaller ones: `SEARCH SINCE` is date-granular and server-local, so the
cursor is asked wide and narrowed in Python; and SMTP files nothing in Sent, so
the client APPENDs the sent copy itself (best-effort — the mail is already
delivered, and failing to file a copy is not a send failure).

Outgoing MIME is shared with the Gmail client (`providers/mime_utils.py`).
That is a function, not a fourth layer: two providers send the same bytes built
from the same record, and Microsoft does not use it at all because Graph takes
JSON.

---

## 6. API Permissions

All permissions are **Delegated** (user context, not application).

| Permission | Purpose |
|------------|---------|
| `openid` | OAuth login |
| `offline_access` | Refresh tokens |
| `User.Read` | Basic profile during OAuth |
| `Mail.ReadWrite` | Create drafts, read Sent Items |
| `Mail.Send` | Send emails from personal mailbox |
| `Mail.ReadWrite.Shared` | Create drafts in shared mailbox |
| `Mail.Send.Shared` | Send emails from shared mailbox |

**No Application Permissions needed** - this module has no admin-level access.

**Note:** For shared mailboxes, users also need **SendAs permission** in Microsoft 365 Exchange Admin Center.

---

## 7. Field Naming Convention

All custom fields use `x_` prefix per Odoo.sh guidelines:
- `x_microsoft_access_token`
- `x_microsoft_mailbox_id`
- `x_pan_outlook_pro.client_id`

---

## 8. Custom Email Headers

Added to outgoing emails for external workflow integration:

| Header | Example | Purpose |
|--------|---------|---------|
| `X-Odoo-Model` | `sale.order` | Source model |
| `X-Odoo-Record-Id` | `123` | Source record ID |
| `X-Odoo-Mail-Id` | `456` | mail.mail record ID |
| `X-Odoo-Message-Id` | `789` | mail.message record ID |

---

## 9. Known Limitations

### Cannot Query SendAs Permissions via Graph API

Microsoft Graph API does not provide an endpoint to query which shared mailboxes a user has SendAs permission for. This is a [known limitation](https://learn.microsoft.com/en-us/answers/questions/1168052/).

**Impact:**
- Cannot automatically show only accessible mailboxes
- Admin adds mailboxes manually in Odoo
- Azure validates permission at send time (returns error if no access)

---

## 10. Requirements Checklist

### Outgoing Email

| Requirement | Status |
|-------------|--------|
| From address selector in composer | Done |
| Personal mailbox support | Done |
| Shared mailbox support | Done |
| Default mailbox per user | Done |
| Auto-create personal mailbox on OAuth | Done |
| Owner-based visibility filtering | Done |
| Correct Message-ID for reply threading | Done |

### Incoming Email

| Requirement | Status |
|-------------|--------|
| Sync from Microsoft 365 mailboxes | Done |
| Reply threading via In-Reply-To | Done |
| 2-way sync (Inbox + Sent Items) | Done |
| Skip Odoo-originated emails | Done |
| Skip history on first sync | Done |
| Activity creation for assignment | Done |
| Known partners only mode | Done |
| Skip internal users (employees) | Done |
| Configurable routing per mailbox | Done |
| Target model selection (Lead/Ticket) | Done |
| Contact block list | Done |
| Unknown contact handling (all sync mode) | Done |
| Smart routing toggle (AI decides) | Placeholder |
| AI triage queue (approval mode) | Future |

### Security

| Requirement | Status |
|-------------|--------|
| OAuth 2.0 with Microsoft Entra ID | Done |
| Delegated permissions only | Done |
| Token encryption at rest | Done |
| Automatic token refresh | Done |

---

## 11. Unit Tests

Tests are in `tests/test_incoming_mail.py`. Run with:

```bash
docker-compose stop odoo
docker-compose run --rm odoo python -m odoo -c /etc/odoo/odoo.conf \
  -d test_db -u pan_mail_pro --test-enable --test-tags=pan_mail_pro --stop-after-init
docker-compose start odoo
```

### Test Coverage

| Test Class | Purpose |
|------------|---------|
| `TestInternalDomain` | Internal domain filtering logic |
| `TestDuplicateDetection` | Duplicate message detection |
| `TestPartnerMatching` | Partner finding and creation |
| `TestAliasRouting` | Email routing via aliases |

### Adding New Tests

1. Add test methods to `tests/test_incoming_mail.py`
2. Use `@tagged('pan_mail_pro', 'post_install', '-at_install')` decorator
3. Extend `TransactionCase` for database tests
4. Tests run in transactions (auto-rollback)

---

## 12. Log Tags

Use these tags when debugging:

| Tag | Purpose |
|-----|---------|
| `[Graph API]` | Microsoft Graph API operations |
| `[Incoming Mail]` | Incoming email sync |
| `[OAuth]` | Authentication operations |

---

## References

- [Microsoft Graph API: Send mail from another user](https://learn.microsoft.com/en-us/graph/outlook-send-mail-from-other-user)
- [Odoo.sh Environment Variables FAQ](https://www.odoo.sh/faq#what-are-the-default-environment-variables)
- [Fernet Encryption](https://cryptography.io/en/latest/fernet/)
