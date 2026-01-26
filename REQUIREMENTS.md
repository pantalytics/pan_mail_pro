# Functional Requirements

This document tracks the functional requirements for the Outlook Pro module and their implementation status.

---

## 1. From Address Selection in Chatter

| # | Requirement | Status |
|---|-------------|--------|
| 1.1 | The From address selector is shown only when composing emails to external recipients (customers, partners, leads). Internal notifications use the standard notification address. | ✅ Done |
| 1.2 | Internal notifications and system messages use the configured notification mailbox (e.g., `notifications@...`) and do not show a From selector. | ✅ Done |
| 1.3 | When the selector is shown, the user clearly sees which From address will be used before sending. | ✅ Done |
| 1.4 | If no valid From address is available, sending is blocked with a clear configuration error. | ✅ Done |

---

## 2. Supported From Address Types and Discovery

| # | Requirement | Status | Notes |
|---|-------------|--------|-------|
| 2.1 | The From dropdown lists the user's personal Microsoft 365 mailbox after successful authentication. | ✅ Done | |
| 2.2 | The dropdown lists shared mailboxes the user has Send As or Send on Behalf permissions for in Microsoft 365. | ✅ Done | Admin configures available mailboxes |
| 2.3 | Shared mailboxes are discovered automatically via Microsoft 365 using the Microsoft Graph API. | ⚠️ **Not Possible** | See [Deviation](#deviation-automatic-mailbox-discovery) below |
| 2.4 | On manual refresh, mailbox availability and permissions are revalidated immediately against Microsoft 365. | ✅ Done | "Test Connection" button |
| 2.5 | Users cannot send emails from any address not present in the dropdown. | ✅ Done | |

### Deviation: Automatic Mailbox Discovery

**Original Requirement:** Shared mailboxes should be discovered automatically via Microsoft Graph API.

**Reality:** Microsoft Graph API does **not provide an endpoint** to query which shared mailboxes a user has SendAs permission for. This is a [known Microsoft limitation](https://learn.microsoft.com/en-us/answers/questions/1168052/can-i-get-a-list-of-all-shared-mailboxes-folders-u).

The only way to query this is via Exchange Online PowerShell (`Get-RecipientPermission`), which is not accessible from Odoo.

**Solution:**
1. Admin manually configures available mailboxes in Odoo (Settings → Outlook Pro → Mailboxes)
2. All configured mailboxes appear in user's dropdown
3. Azure validates SendAs permission at send time
4. If user lacks permission, a clear error is shown

This approach is documented in [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md#2-mailbox-permission-management).

---

## 3. From, Reply-To, and Headers

| # | Requirement | Status |
|---|-------------|--------|
| 3.1 | The From header equals the selected email address exactly. | ✅ Done |
| 3.2 | The Reply-To header equals the same selected email address exactly. | ✅ Done |
| 3.3 | No Odoo-generated aliases or technical reply addresses are used in From or Reply-To. | ✅ Done |
| 3.4 | Each outgoing email includes custom headers containing Odoo model name, record ID, and source type. | ✅ Done |
| 3.5 | Header naming follows a clear and documented convention. | ✅ Done |

### Custom Headers

| Header | Example | Purpose |
|--------|---------|---------|
| `X-Odoo-Model` | `sale.order` | Source model |
| `X-Odoo-Record-Id` | `123` | Source record ID |
| `X-Odoo-Mail-Id` | `456` | mail.mail record ID |
| `X-Odoo-Message-Id` | `789` | mail.message record ID |

---

## 4. Authentication and Security

| # | Requirement | Status |
|---|-------------|--------|
| 4.1 | Authentication uses OAuth 2.0 with Microsoft Entra ID and complies with current Microsoft security standards. | ✅ Done |
| 4.2 | The authentication approach follows Microsoft best practices and is documented. | ✅ Done |
| 4.3 | No basic authentication, app passwords, or legacy protocols are used. | ✅ Done |
| 4.4 | Tokens are stored securely and refreshed automatically. | ✅ Done |
| 4.5 | If authentication fails or a token expires and cannot be refreshed, sending is blocked with a hard error prompting re-authentication. | ✅ Done |

### Security Implementation

- **OAuth 2.0** with delegated permissions (user context)
- **Token encryption** using Fernet symmetric encryption (AES-128)
- **Automatic token refresh** before expiry
- **No SMTP** - all email via Microsoft Graph API

See [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md#1-token-encryption) for detailed security rationale.

---

## 5. Default From Address

| # | Requirement | Status |
|---|-------------|--------|
| 5.1 | Each user can configure a personal default From address in their user preferences. | ✅ Done |
| 5.2 | The default From address is preselected when composing an email. | ✅ Done |
| 5.3 | The user can override the default per email. | ✅ Done |
| 5.4 | If the default address becomes unavailable, sending is blocked with a hard error explaining the issue. | ✅ Done |

**Location:** My Profile → Preferences → Email tab

---

## 6. Auditing and Logging

| # | Requirement | Status |
|---|-------------|--------|
| 6.1 | Each sent email stores the selected From address on the Odoo message record. | ✅ Done |
| 6.2 | Errors related to authentication, permissions, or Microsoft API failures are logged with actionable error messages. | ✅ Done |
| 6.3 | Logging and audit behavior follows Odoo and Microsoft best practices and is documented. | ✅ Done |

### Log Tags

- `[Graph API]` - Microsoft Graph API operations
- `[Incoming Mail]` - Incoming email sync operations
- `[OAuth]` - Authentication operations

---

## 7. Error Handling and UX

| # | Requirement | Status |
|---|-------------|--------|
| 7.1 | Sending is hard-blocked if no valid From address is selected or available. | ✅ Done |
| 7.2 | Sending is hard-blocked if the user lacks permission to send from the selected mailbox. | ✅ Done |
| 7.3 | Microsoft API or permission errors are clearly communicated to the user with guidance on next steps. | ✅ Done |
| 7.4 | No automatic fallback to another mailbox is performed. | ✅ Done |

---

## 8. Incoming Email Sync (Added Scope)

These features were originally marked as "out of scope" but have been implemented based on customer needs.

| # | Requirement | Status |
|---|-------------|--------|
| 8.1 | Inbound emails are synced from Microsoft 365 mailboxes to Odoo. | ✅ Done |
| 8.2 | Replies are automatically threaded to the original Odoo record via In-Reply-To header. | ✅ Done |
| 8.3 | Emails sent directly from Outlook (not via Odoo) can also sync back (2-way sync). | ✅ Done |
| 8.4 | Unknown senders automatically create partner records with correct name/email. | ✅ Done |
| 8.5 | First sync only sets timestamp - no historical emails are imported. | ✅ Done |
| 8.6 | Sync interval is configurable (default: 1 minute). | ✅ Done |

### Sync Modes

| Mode | Inbox | Sent Items | Use Case |
|------|-------|------------|----------|
| No sync (outgoing only) | - | - | Notification mailbox |
| Received emails only | ✅ | - | Basic incoming sync |
| Received + Sent from Outlook | ✅ | ✅ | Full 2-way sync |

See [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md#7-incoming-email-architecture) for architecture details.

---

## Out of Scope

The following remain out of scope:

- Custom mail servers outside Microsoft 365
- Support for Google Workspace / Gmail
- Public folder access
- Calendar integration

---

## Document History

| Date | Change |
|------|--------|
| 2024-01 | Initial requirements (sections 1-7) |
| 2024-01 | Added incoming email sync (section 8) |
| 2024-01 | Documented Graph API limitation for mailbox discovery (section 2.3) |
