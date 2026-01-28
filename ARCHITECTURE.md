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

## 3. Email Flows

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
│ _convert_to_rfc2822()                        │
│ - Preserve Message-ID                        │
│ - Preserve In-Reply-To                       │
└─────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────┐
│ mail.thread.message_process()  ← NATIVE      │
│                                              │
│ 1. Parse email headers                       │
│ 2. Check In-Reply-To → find parent message   │
│ 3. If reply: post to existing record         │
│ 4. If new: find/create partner, post there   │
└─────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────┐
│ mail.message created                         │
│ + optional mail.activity                     │
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

## 4. Design Decisions

### 4.1 Token Encryption

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

### 4.2 Polling over Webhooks

**Decision:** Poll Microsoft Graph API every 1 minute instead of using webhooks.

**Why:**
- Works out-of-the-box on Odoo.sh (no public endpoint needed)
- Reuses existing OAuth token infrastructure
- 1-minute delay acceptable for email sync

### 4.3 Native mail.thread.message_process()

**Decision:** Use Odoo's native `message_process()` instead of custom inbox model.

**Why:**
- Proven Odoo code for threading, attachments, partner creation
- Standard data structures = easier AI integration later
- Less custom code = less maintenance

### 4.4 Reply Threading via Microsoft Message-ID

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

### 4.5 First Sync Skips History

**Decision:** On first sync, set timestamp to "now" and skip fetching.

**Why:**
- Prevents importing hundreds of old emails
- Prevents flooding chatter with historical messages
- Clean start for new mailbox configuration

### 4.6 Pre-create Partners

**Decision:** Find/create partner BEFORE calling `message_process()`.

**Why:**
- Odoo's auto-creation sometimes uses email subject as partner name
- Pre-creation ensures correct name from email "From" header

### 4.7 Personal Mailbox Auto-creation

**Decision:** Auto-create personal mailbox when user connects Microsoft account.

**Why:**
- Zero-friction onboarding for users
- Admin can disable via setting if not wanted
- Mailbox immediately available in composer dropdown

### 4.8 Shared Mailbox: User's Own Token

**Decision:** Each user sends from shared mailbox using their own OAuth token.

**Why:**
- Principle of least privilege
- No centralized "super user" needed
- Audit trail shows actual sender
- User needs proper M365 permissions anyway

---

## 5. API Permissions

All permissions are **Delegated** (user context, not application).

| Permission | Purpose |
|------------|---------|
| `openid` | OAuth login |
| `profile` | User profile info |
| `email` | User email address |
| `offline_access` | Refresh tokens |
| `User.Read` | Basic profile during OAuth |
| `Mail.Send` | Send from own mailbox |
| `Mail.Send.Shared` | Send from shared mailboxes |
| `Mail.Read` | Fetch Message-ID from Sent Items |
| `Mail.Read.Shared` | Read shared mailbox for sync |

**No Application Permissions needed** - this module has no admin-level access.

---

## 6. Field Naming Convention

All custom fields use `x_` prefix per Odoo.sh guidelines:
- `x_microsoft_access_token`
- `x_microsoft_mailbox_id`
- `x_pan_outlook_pro.client_id`

---

## 7. Custom Email Headers

Added to outgoing emails for external workflow integration:

| Header | Example | Purpose |
|--------|---------|---------|
| `X-Odoo-Model` | `sale.order` | Source model |
| `X-Odoo-Record-Id` | `123` | Source record ID |
| `X-Odoo-Mail-Id` | `456` | mail.mail record ID |
| `X-Odoo-Message-Id` | `789` | mail.message record ID |

---

## 8. Known Limitations

### Cannot Query SendAs Permissions via Graph API

Microsoft Graph API does not provide an endpoint to query which shared mailboxes a user has SendAs permission for. This is a [known limitation](https://learn.microsoft.com/en-us/answers/questions/1168052/).

**Impact:**
- Cannot automatically show only accessible mailboxes
- Admin adds mailboxes manually in Odoo
- Azure validates permission at send time (returns error if no access)

---

## 9. Requirements Checklist

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
| Auto-create partners | Done |
| Skip history on first sync | Done |
| Activity creation for assignment | Done |

### Security

| Requirement | Status |
|-------------|--------|
| OAuth 2.0 with Microsoft Entra ID | Done |
| Delegated permissions only | Done |
| Token encryption at rest | Done |
| Automatic token refresh | Done |

---

## 10. Log Tags

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
