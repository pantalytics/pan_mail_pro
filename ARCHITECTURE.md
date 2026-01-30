# Architecture

Technical documentation for developers working on the Outlook Pro module.

---

## 1. Overview

### Purpose
Complete Microsoft 365 email integration for Odoo - send and receive emails via Microsoft Graph API with proper threading and partner management.

### Key Models

| Model | Purpose |
|-------|---------|
| `x_microsoft.mailbox` | Mailbox configuration (email, type, sync settings) |
| `microsoft.graph.client` | Graph API helper (all API calls) |
| `microsoft.incoming.mail.processor` | Incoming email sync (cron) |
| `mail.mail` | Outgoing email override (Graph API send) |
| `mail.compose.message` | Composer "Send From" dropdown |
| `res.users` | User OAuth tokens |
| `res.config.settings` | Module settings (client_id, secret, tenant) |

### Module Structure

```
pan_outlook_pro/
├── models/
│   ├── mail_mail.py              # Outgoing email override
│   ├── mail_compose_message.py   # Composer integration
│   ├── microsoft_mailbox.py      # Mailbox configuration
│   ├── microsoft_incoming_mail.py # Incoming email processor
│   ├── microsoft_graph_client.py  # Graph API client
│   ├── res_users.py              # User OAuth tokens
│   ├── res_config_settings.py    # Module settings
│   └── encryption_utils.py       # Fernet encryption
├── controllers/
│   └── main.py                   # OAuth callback handler
├── wizard/
│   └── microsoft_oauth_wizard.py # Connect Microsoft account
├── views/
├── data/
│   └── ir_cron_data.xml          # Incoming mail cron (1 min)
└── security/
```

---

## 2. Mailbox Types

### Overview

| Type | Who sees it? | Who's OAuth token? | Use case |
|------|--------------|-------------------|----------|
| **Personal** | Only owner | Owner's token | User's own mailbox (john@company.com) |
| **Shared** | Everyone | Sender's own token | Team mailbox (sales@company.com) |
| **Notification** | Everyone | Owner's token | System emails (notifications@company.com) |

### Graph API Endpoints per Mailbox Type

| Type | Send Endpoint | Sent Items Location | Required Permissions |
|------|---------------|---------------------|----------------------|
| **Personal** | `/me/sendMail` | User's own Sent Items | `Mail.Send` |
| **Shared** | `/users/{email}/sendMail` | Shared mailbox Sent Items | `Mail.Send.Shared` + SendAs in Exchange |
| **Notification** | `/users/{email}/sendMail` | Notification mailbox Sent Items | `Mail.Send.Shared` + SendAs in Exchange |

**Note:**
- `{email}` = the mailbox email address (e.g., `team1@company.com`). Microsoft Graph also accepts Object ID or UPN, but we use the email address.
- For shared and notification mailboxes, using `/users/{email}/sendMail` stores the sent email in the mailbox's Sent Items folder (not the user's personal Sent Items). This is the preferred behavior for team visibility.

### Personal Mailbox

- Auto-created when user connects Microsoft account (if admin setting allows)
- `x_owner_user_id` field links mailbox to owner
- Only visible to owner in composer dropdown
- Owner sends with their own OAuth token

### Shared Mailbox

- Visible to all users in composer dropdown
- Each user sends with their **own** OAuth token
- User needs:
  - `Mail.Send.Shared` permission in Azure
  - "Send As" rights on the mailbox in Microsoft 365

### Notification Mailbox

- For system notifications (activity reminders, mentions to internal users)
- Uses the Owner's OAuth token (same field as personal mailboxes)
- Only one active notification mailbox allowed

---

## 3. Sync Modes

### Overview

Each mailbox can be configured with a sync mode that determines how incoming emails are handled.

| Mode | Inbox | Sent Items | Filter | Use Case |
|------|-------|------------|--------|----------|
| **Send only** | - | - | - | Send-only mailbox |
| **Known partners** | ✓ | ✓ | Existing partners only | Safe default, no spam |

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
- Simple "Internal Domains" setting in Outlook Pro configuration
- Explicit control over which domains are excluded
- Internal employees filtered via domain (Inbox) OR via `partner.user_ids` (both folders)
- Replies always work (partner was created when we sent to them)
- Spam/marketing naturally filtered (not in contacts)
- Simple to understand and maintain

### Roadmap: Email Routing Options

**Status:** Planned for v1.1

**Current behavior:** New incoming emails are posted to the partner's chatter.

**Planned:** Add configurable routing per mailbox to automatically create records:

| Route Option | Target Model | Use Case |
|--------------|--------------|----------|
| Contact Chatter | `res.partner` | Current behavior |
| **CRM Lead** | `crm.lead` (type=lead) | Default for sales mailboxes |
| CRM Opportunity | `crm.lead` (type=opportunity) | Direct pipeline |
| Helpdesk Ticket | `helpdesk.ticket` | Support mailboxes |

**Routing logic:**
```
Email arrives
    │
    ▼
Reply check (In-Reply-To header)
    │
    ├── Match found → Post to existing thread (any model)
    │
    └── No match → Route based on mailbox setting:
                   → CRM Lead (default for new conversations)
                   → Helpdesk Ticket
                   → Partner Chatter (fallback)
```

**Benefits for AI integration:**
- Each conversation = 1 Lead record
- Leads linked to `partner_id` → aggregate per company
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

| Check | Header/Condition | Action |
|-------|------------------|--------|
| Odoo-originated | `X-Odoo-Model` or `X-Odoo-Mail-Id` present | Skip |
| Duplicate | `Message-ID` already in Odoo | Skip |

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
                           │ Post to contact's chatter       │
                           │ (res.partner)                   │
                           └─────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────┐
│ mail.message created                         │
│ + optional mail.activity (new threads only)  │
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

### 5.3 Native mail.thread.message_process()

**Decision:** Use Odoo's native `message_process()` instead of custom inbox model.

**Why:**
- Proven Odoo code for threading, attachments, partner creation
- Standard data structures = easier AI integration later
- Less custom code = less maintenance

### 5.4 Reply Threading via Microsoft Message-ID

**Decision:** After sending, fetch actual Message-ID from Sent Items.

**Why:**
- Graph API `sendMail` doesn't return Message-ID
- Microsoft generates its own Message-ID (different from any we set)
- Need Microsoft's ID for correct reply threading

**Implementation:**
```python
time.sleep(1)  # Wait for message in Sent Items
actual_message_id = self._fetch_sent_message_id(token, subject)
```

### 5.5 First Sync Skips History

**Decision:** On first sync, set timestamp to "now" and skip fetching.

**Why:**
- Prevents importing hundreds of old emails
- Prevents flooding chatter with historical messages
- Clean start for new mailbox configuration

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

## 6. API Permissions

All permissions are **Delegated** (user context, not application).

| Permission | Purpose |
|------------|---------|
| `openid` | OAuth login |
| `offline_access` | Refresh tokens |
| `User.Read` | Basic profile during OAuth |
| `Mail.ReadWrite` | Send emails (via draft), read Sent Items |
| `Mail.ReadWrite.Shared` | Shared mailbox: send + read |

**No Application Permissions needed** - this module has no admin-level access.

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
| Full sync mode (with triage) | Future |

### Security

| Requirement | Status |
|-------------|--------|
| OAuth 2.0 with Microsoft Entra ID | Done |
| Delegated permissions only | Done |
| Token encryption at rest | Done |
| Automatic token refresh | Done |

---

## 11. Log Tags

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
