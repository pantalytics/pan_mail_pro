# Design Decisions

This document captures key architectural and design decisions for future reference.

---

## 1. Token Encryption

### Decision
Use Fernet symmetric encryption with auto-generated key stored in database.

### Context
OAuth tokens (access_token, refresh_token) and client_secret need protection. Options considered:

| Option | Pros | Cons |
|--------|------|------|
| Plain text in database | Simple | Insecure, tokens visible in backups |
| Environment variable for key | Key separate from data | Odoo.sh doesn't support custom env vars |
| Auto-generated key in database | Zero-config, works on Odoo.sh | Key in same DB as encrypted data |
| External key management (Vault) | Most secure | Complex, expensive, overkill for this use case |

### Rationale
- Odoo.sh only provides [built-in environment variables](https://www.odoo.sh/faq#what-are-the-default-environment-variables), no custom ones
- Database is already encrypted at rest on Odoo.sh
- Encryption provides defense against: SQL injection, backup leaks, casual DB browsing
- Acceptable for ISO 27001 compliance with database-level encryption
- Zero configuration required for end users

### Implementation
- Key stored in `ir.config_parameter` with name `x_pan_outlook_pro.encryption_key`
- Auto-generated on first use via `Fernet.generate_key()`
- All encryption/decryption via `models/encryption_utils.py`

---

## 2. Mailbox Permission Management

### Decision
Admin maintains list of mailboxes in Odoo. Azure validates permissions at send time.

### Context
We need to show users which mailboxes they can send from. Options considered:

| Option | Description | Feasibility |
|--------|-------------|-------------|
| Query Graph API for SendAs permissions | Automatic, Azure is source of truth | **Not possible** - Graph API doesn't support this |
| Admin assigns mailboxes to users in Odoo | Manual but accurate | Possible, creates double administration |
| Show all mailboxes, Azure validates | Simple, some UX friction | Current approach |

### Rationale
**Microsoft Graph API limitation:** There is no endpoint to query which shared mailboxes a user has SendAs permission for. This is a [known feature request](https://learn.microsoft.com/en-us/answers/questions/1168052/can-i-get-a-list-of-all-shared-mailboxes-folders-u) that Microsoft has not implemented.

The only way to query this is via Exchange Online PowerShell (`Get-RecipientPermission`), which is not accessible from Odoo.

### Current Implementation
1. Admin adds mailboxes in Settings > Microsoft Mailboxes
2. User sees all mailboxes in composer dropdown
3. If user selects mailbox without Azure permission → Graph API returns error
4. Odoo shows clear error message: "You don't have permission for this mailbox"

### Future Option
Add `allowed_user_ids` field to `x_microsoft.mailbox` model so admin can restrict visibility per user. This creates double administration (Azure + Odoo) but improves UX.

---

## 3. OAuth Flow Location

### Decision
OAuth configuration in Settings (admin), OAuth connect in User Preferences (user).

### Context
Users were confused by "Save changes?" popup when clicking Connect button in Settings.

### Rationale
- Admin configures Azure credentials once (client_id, secret, tenant_id)
- Each user connects their own Microsoft account from Preferences
- Clean separation of concerns
- No unsaved changes popup when connecting

### Implementation
- `res_config_settings_views.xml`: Azure credentials configuration
- `res_users_views.xml`: Connect button in Preferences tab

---

## 4. Field Security (groups='base.group_system')

### Decision
Encrypted token fields are restricted to system group, with `sudo()` for internal operations.

### Context
Token fields should not be readable/writable by normal users via UI or API.

### Implementation
```python
x_microsoft_access_token_encrypted = fields.Char(
    groups='base.group_system',  # Only admins can see in UI
    ...
)
```

Internal code uses `sudo()` to write tokens:
```python
# Safe: only writes to current user's own record
self.env.user.sudo().write({'x_microsoft_access_token': token})
```

### Security Note
`sudo()` bypasses Odoo's record rules but:
- Users cannot execute arbitrary Python code
- Code only writes to `self.env.user` (own record)
- Never writes to arbitrary user IDs

---

## 5. Custom Email Headers

### Decision
Add X-Odoo-* headers to outgoing emails for external workflow integration.

### Context
Customer uses n8n to process email replies and route them back to Odoo.

### Headers Added
| Header | Value | Purpose |
|--------|-------|---------|
| `X-Odoo-Model` | e.g., `sale.order` | Source model |
| `X-Odoo-Record-Id` | e.g., `123` | Source record ID |
| `X-Odoo-Mail-Id` | e.g., `456` | mail.mail record ID |
| `X-Odoo-Message-Id` | e.g., `789` | mail.message record ID |

### Implementation
Uses Microsoft Graph API's `internetMessageHeaders` field in sendMail payload.

---

## 6. Parameter Naming Convention

### Decision
All custom system parameters use `x_` prefix.

### Context
Odoo.sh recommends `x_` prefix for custom fields/parameters to avoid conflicts with standard Odoo modules.

### Examples
- `x_pan_outlook_pro.client_id`
- `x_pan_outlook_pro.client_secret_encrypted`
- `x_pan_outlook_pro.encryption_key`

---

## 7. Incoming Email Architecture

### Decision
Use polling-based sync with Microsoft Graph API, routing emails via Odoo's **native `mail.thread.message_process()`** instead of a custom inbox model.

### Context
Customer needs:
- Full visibility of all email communication in Odoo
- 2-way sync (emails sent via Outlook should also appear in Odoo)
- Team assignment for incoming emails
- Auto-create contacts for unknown senders
- AI-ready: use standard Odoo data structures

Options considered:

| Option | Pros | Cons |
|--------|------|------|
| Custom `x_microsoft.inbox` model | Full control, custom UI | Duplicates Odoo functionality, more maintenance |
| **Native `mail.message` + `mail.activity`** | Uses proven Odoo code, AI-ready | Less custom control |
| Microsoft Graph Webhooks | Real-time | Requires public HTTPS endpoint |
| n8n workflow | No Odoo code changes | Extra dependency |

### Rationale
**Native Odoo mail system chosen** because:
- `mail.message` already has threading, read/unread, attachments
- `mail.notification` already tracks who read what
- `mail.activity` provides native assignment + deadlines
- Standard Odoo data = easier AI integration later
- Less custom code = less maintenance
- `message_process()` handles partner finding/creation automatically

**Polling chosen over webhooks** because:
- Works out-of-the-box on Odoo.sh (no public endpoint needed)
- Reuses existing OAuth token infrastructure
- 1-minute delay acceptable for this use case (configurable via cron)

### Email Flow
```
Microsoft Graph API (polling every 1 min)
      │
      ▼
┌─────────────────────────────┐
│ fetch_messages()            │
│ - Inbox + Sent Items        │
└─────────────────────────────┘
      │
      ▼
┌─────────────────────────────┐
│ _convert_to_rfc2822()       │
│ - Preserve Message-ID       │
│ - Preserve In-Reply-To      │
└─────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────┐
│ mail.thread.message_process()  ← NATIVE!    │
│                                             │
│ 1. Parse email headers                      │
│ 2. Check In-Reply-To → find parent message  │
│ 3. If reply: post to existing record        │
│ 4. If new: find/create partner, post there  │
└─────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────┐
│ mail.message created        │
│ - Visible in chatter        │
└─────────────────────────────┘
      │
      ▼ (optional)
┌─────────────────────────────┐
│ mail.activity created       │
│ - "Review incoming email"   │
│ - Assigned to team member   │
└─────────────────────────────┘
```

### What We Get for Free (from Odoo Native)
- ✅ Threading via `mail.message.parent_id`
- ✅ Read/unread via `mail.notification.is_read`
- ✅ Partner auto-creation via `message_process()`
- ✅ Attachments handling
- ✅ Chatter integration
- ✅ Message search
- ✅ Activities for assignment

### API Permissions Required
```
Mail.Read           - Read inbox messages
Mail.Read.Shared    - Read shared mailbox messages
```

Note: Existing users need to reconnect Microsoft account to grant new permissions.

---

## 8. Reply Threading via Microsoft Message-ID

### Decision
After sending an email via Graph API, fetch the actual Message-ID from Sent Items and store it in Odoo.

### Context
When Odoo sends an email via Microsoft Graph API's `sendMail` endpoint:
1. Graph API does NOT return the Message-ID in the response
2. Microsoft generates its own Message-ID (different from any we might set)
3. When recipient replies, their email has `In-Reply-To` pointing to Microsoft's Message-ID
4. Odoo needs to match this to route the reply to the correct thread

### Implementation
```python
# After successful send
time.sleep(1)  # Wait for message to appear in Sent Items
actual_message_id = self._fetch_sent_message_id(token, subject)
# Store in mail.mail record for threading
```

The `_fetch_sent_message_id` method:
1. Queries `/me/mailFolders/SentItems/messages`
2. Filters by subject and recent timestamp
3. Returns the `internetMessageId` field

### Why 1 second delay?
Microsoft Graph API is eventually consistent. The message may not appear in Sent Items immediately after `sendMail` returns success. A 1-second delay provides sufficient time for propagation.

---

## 9. First Sync Behavior

### Decision
On first sync, set `x_last_sync_date` to "now" and skip fetching. This prevents importing historical emails.

### Context
When a mailbox is first configured for incoming sync, fetching all historical emails would:
- Create hundreds of contacts
- Flood the chatter with old messages
- Take a long time to process

### Implementation
```python
if not mailbox.x_last_sync_date:
    mailbox.write({'x_last_sync_date': fields.Datetime.now()})
    return  # Skip this run
```

The Graph API filter `receivedDateTime gt {timestamp}` ensures only emails after this date are fetched.

### Alternative Considered
Adding a separate `x_initial_sync_date` field. Rejected as overly complex - the existing `x_last_sync_date` field serves the purpose.

---

## 10. Partner Creation Before message_process

### Decision
Pre-create or find the partner BEFORE calling `message_process()` to ensure correct name and email.

### Context
Odoo's `message_process()` auto-creates partners for unknown senders. However, it sometimes uses the email subject as the partner name instead of the sender's display name.

### Implementation
```python
# Extract sender info from Graph message
from_email = full_message.get('from', {}).get('emailAddress', {}).get('address')
from_name = full_message.get('from', {}).get('emailAddress', {}).get('name')

# Pre-create partner with correct data
partner = self._find_or_create_partner(from_email, from_name)

# Then call message_process - it will find the existing partner
```

This ensures partners always have the correct name from the email's "From" header.

---

## 11. Stored Computed Field for Domain Filtering

### Decision
`x_microsoft_oauth_connected` is a stored computed field (`store=True`) to enable domain filtering.

### Context
The mailbox "Sync As User" field needs a domain filter to show only users with Microsoft OAuth:
```python
domain="[('x_microsoft_oauth_connected', '=', True)]"
```

Odoo cannot use non-stored computed fields in domain filters.

### Implementation
```python
x_microsoft_oauth_connected = fields.Boolean(
    compute='_compute_microsoft_oauth_connected',
    store=True  # Required for domain filtering
)

@api.depends('x_microsoft_refresh_token_encrypted')
def _compute_microsoft_oauth_connected(self):
    for user in self:
        user.x_microsoft_oauth_connected = bool(user.x_microsoft_refresh_token_encrypted)
```

The field recomputes automatically when the refresh token changes.

---

## 12. No SMTP Fallback - All Emails via Graph API

### Decision
All outgoing emails go through Microsoft Graph API. No SMTP fallback.

### Context
Using SMTP alongside Graph API creates:
- Configuration complexity (need to set up SMTP server)
- Inconsistent sender addresses
- "Connection Failed" errors when SMTP not configured

### Email Routing Logic
```
Email to send
      │
      ├── Has explicit mailbox set?
      │   └── Yes → Use that mailbox + author's user
      │
      ├── Is system notification (is_notification=True)?
      │   └── Yes → Use notification mailbox + notification user (from Settings)
      │
      └── User email without mailbox
          └── Use user's default mailbox + that user
```

### Implementation
Three helper methods in `mail.mail`:
- `_get_mailbox_and_user()` - Determines mailbox and sending user
- `_get_notification_mailbox_and_user()` - Gets system config
- `_get_missing_mailbox_error()` / `_get_missing_user_error()` - Clear error messages

### Configuration Required
1. **Admin:** Configure notification mailbox + user in Settings → Outlook Pro
2. **Users:** Set default mailbox in My Profile → Preferences → Email

### Error Handling
Clear error messages guide users to fix configuration:
- Missing notification mailbox → "Go to Settings → Outlook Pro"
- Missing user mailbox → "Go to My Profile → Preferences"
- Missing OAuth → "Connect your Microsoft account"

---

## 13. Go-to-Market Strategy

### Decision
Phased approach: Start with flat pricing in App Store, iterate based on feedback, then move to IAP subscription model.

### Context
The module is ready for commercial release. Key constraints:
- **Odoo Online (SaaS):** Cannot install third-party Python modules
- **Odoo.sh & On-premise:** Can install any module
- **Target market:** ~40% of Odoo Enterprise users (Odoo.sh + self-hosted)

### Phased Rollout

```
Phase 1: Test Launch (Current)
├── Flat pricing in Odoo App Store (€99 or €149)
├── Note in description: "Pricing model may change"
├── Goal: 10 sales + feedback collection
└── Learn: What do customers struggle with?

Phase 2: Feedback & Iteration
├── Analyze support tickets
├── Improve documentation
├── Fix edge cases
└── Refine pricing based on value delivered

Phase 3: IAP Credit Model
├── Switch to usage-based pricing
├── 1 credit = 1 user × 1 month
├── Trial: 12 free credits (1 user for 1 year)
├── Price: €5/user/month
└── Credit packs: 12/60/120 credits

Phase 4: Cloud Migration Upsell
├── Target: Odoo Online users who want shared mailboxes
├── Offer: Migration to Odoo.sh + module setup
├── Service: €500-1500 (consultancy)
└── Recurring: IAP credits + optional support contract
```

### Platform Compatibility

| Platform | Install Module? | Our Solution |
|----------|-----------------|--------------|
| Odoo Online (SaaS) | ❌ No | Upsell migration to Odoo.sh |
| Odoo.sh (PaaS) | ✅ Yes | Primary target |
| On-premise | ✅ Yes | Secondary target |

### IAP Credit Model Details (Phase 3)

**Pricing:**
- €5 per user per month
- 12 credits = €60 (we receive €45 after 25% Odoo commission)
- 60 credits = €300 (we receive €225)
- 120 credits = €600 (we receive €450)

**Enforcement:**
- Hard block when credits depleted
- Monthly cron (1st of month): count OAuth-connected users, deduct credits
- User definition: `x_microsoft_oauth_connected = True`

**Trial:**
- 12 free credits for new installations
- Allows 1 user for 12 months, or 12 users for 1 month

### Revenue Model

```
Per Customer (estimated):

Phase 1 (Flat):
  App sale: €149 → €104 for us (70%)

Phase 3 (IAP):
  5 users × €5 × 12 months = €300/year → €225 for us (75%)

Phase 4 (Upsell):
  Migration service: €750 → €750 for us (100%)
  + IAP recurring
```

### Cloud User Upsell Pitch

When Odoo Online users ask about shared mailbox support:

> "Shared mailboxes require custom code which isn't available on Odoo Online.
> We can help you migrate to Odoo.sh where you get full flexibility.
>
> Our migration service includes:
> - Database migration to Odoo.sh
> - Outlook Pro module installation
> - Azure app configuration
> - Mailbox setup + testing
> - 1 hour training call
>
> After migration, you pay €5/user/month for the module."

### Success Metrics

| Phase | Metric | Target |
|-------|--------|--------|
| 1 | Sales | 10 customers |
| 1 | Support tickets | Track common issues |
| 2 | Customer satisfaction | >4/5 rating |
| 3 | MRR (Monthly Recurring Revenue) | €500/month |
| 4 | Migration conversions | 2/quarter |

---

## References

- [Microsoft Graph API: Send mail from another user](https://learn.microsoft.com/en-us/graph/outlook-send-mail-from-other-user)
- [Odoo.sh Environment Variables FAQ](https://www.odoo.sh/faq#what-are-the-default-environment-variables)
- [Fernet Encryption (cryptography library)](https://cryptography.io/en/latest/fernet/)
- [Odoo Inbound Messages Documentation](https://www.odoo.com/documentation/18.0/applications/general/email_communication/email_servers_inbound.html)
- [Odoo Apps Store FAQ](https://apps.odoo.com/apps/faq)
- [Odoo IAP Documentation](https://www.odoo.com/documentation/18.0/applications/essentials/in_app_purchase.html)
