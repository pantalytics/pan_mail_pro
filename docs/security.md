# Security

Mail Pro is designed with security as a priority, following the principle of least privilege.

## Authentication

### OAuth 2.0 with Delegated Permissions

Mail Pro uses **delegated permissions only** - the app acts on behalf of the signed-in user, never as an administrator.

| Aspect | Implementation |
|--------|----------------|
| Protocol | OAuth 2.0 Authorization Code Flow |
| Identity Provider | Microsoft Entra ID (Azure AD) |
| Permission Type | Delegated only |
| Token Lifetime | Access: 1 hour, Refresh: 90 days |

### Required Permissions

**Personal mailbox:**

| Permission | Purpose |
|------------|---------|
| `Mail.ReadWrite` | Create drafts, read emails |
| `Mail.Send` | Send emails |
| `offline_access` | Obtain refresh tokens |
| `User.Read` | Read user profile for identification |

**Shared mailbox (additional):**

| Permission | Purpose |
|------------|---------|
| `Mail.ReadWrite.Shared` | Create drafts in shared mailbox |
| `Mail.Send.Shared` | Send from shared mailbox |

**Note:** All permissions are delegated. Users authorize their own accounts.

## Token Security

### Encryption at Rest

All OAuth tokens are encrypted before storage:

- **Algorithm:** Fernet (AES-128-CBC with HMAC)
- **Key:** Derived from Odoo database UUID
- **Storage:** Encrypted in PostgreSQL

### Token Handling

- Access tokens are short-lived (1 hour)
- Refresh tokens are automatically rotated
- Tokens are never logged or exposed in error messages
- Tokens are cleared on disconnect

## Shared Mailbox Access

For shared mailboxes:

- Each user authenticates with **their own** Microsoft account
- Users need **SendAs permission** granted in Microsoft 365
- No shared credentials or service accounts

## Data Flow

```
User → Odoo → Microsoft Graph API → Microsoft 365
         ↑
    OAuth Token
   (encrypted)
```

1. User initiates action in Odoo
2. Odoo retrieves encrypted token
3. Token decrypted in memory
4. API call made to Microsoft Graph
5. Response processed in Odoo

## Audit Trail

All email operations are logged:

- Sent emails tracked with Microsoft message ID
- Sync operations logged with `[Incoming Mail]` tag
- API errors logged with `[Graph API]` tag

## Compliance

- **GDPR:** User data processed per Microsoft's data processing terms
- **Data residency:** Determined by Microsoft 365 tenant configuration
- **Odoo data:** Stored in your Odoo database location
