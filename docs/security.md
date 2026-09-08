# Security

Mail Pro is designed with security as a priority, following the principle of least privilege.

## Authentication

Three providers, two kinds of credential:

| Provider | Credential | Who holds it |
|----------|------------|--------------|
| Microsoft 365 | OAuth 2.0, delegated permissions only | Each user signs in; a shared address is sent *as* with the user's own token |
| Google Workspace | OAuth 2.0, delegated permissions only | Each user signs in; a shared address is its own Workspace account, authorized once |
| IMAP/SMTP | Server, login and password | Entered by an administrator, per address |

### OAuth 2.0 with Delegated Permissions

On Microsoft 365 and Google Workspace the app acts on behalf of the signed-in
user, never as an administrator.

| Aspect | Implementation |
|--------|----------------|
| Protocol | OAuth 2.0 Authorization Code Flow |
| Identity Provider | Microsoft Entra ID (Azure AD) or Google |
| Permission Type | Delegated only |
| Token Lifetime | Access: 1 hour, Refresh: provider policy (Microsoft: 90 days of inactivity) |

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

Every credential the module holds — OAuth access and refresh tokens, IMAP/SMTP
passwords, and the provider's own client secret — is encrypted before storage:

- **Algorithm:** Fernet (AES-128-CBC with HMAC)
- **Key:** A random 32-byte key generated on first use and stored in
  `ir.config_parameter` under `pan_mail_pro.encryption_key` (renamed from
  `x_pan_outlook_pro.encryption_key` by the 19.0.6.0.0 migration; the code
  still adopts a key found under the old name rather than minting a new one)
- **Storage:** Encrypted in PostgreSQL

> **Key and ciphertext live in the same database.** A database dump therefore
> contains both, which makes any backup of this database credential material and
> means it must be handled to the same standard as the tokens themselves. Restoring
> a production dump into a test environment carries working OAuth tokens with it;
> revoke or clear `pan.mail.account` rows after such a restore.
>
> An earlier version of this document stated the key was derived from the Odoo
> database UUID. That was never what the code did (`models/encryption_utils.py`),
> and derivation from a value stored in the same database would not have changed
> the property above.

For deployments that need the key held outside the database, set the
`PAN_MAIL_ENCRYPTION_KEY` environment variable; when present it takes precedence
over the stored parameter and nothing is written to `ir.config_parameter`.

### Token Handling

- Access tokens are short-lived (1 hour) and refreshed automatically
- A refresh token is replaced when the provider issues a new one; Google
  usually does not, so the stored one is kept
- Tokens are never logged or exposed in error messages
- Tokens are cleared on disconnect, and removed altogether when the database
  is neutralized (a staging or test copy)

## Shared Mailbox Access

- **Microsoft 365:** each user authenticates with their own account and needs
  **SendAs permission** on the shared address. No shared credentials.
- **Google Workspace:** the shared address is its own Workspace account,
  authorized once by an administrator. Its refresh token is stored like any
  other, with no Odoo user attached.
- **IMAP/SMTP:** the address's login and password, entered by an administrator
  and stored encrypted.

## Data Flow

```
User → Odoo → Microsoft Graph API / Gmail API / IMAP+SMTP host
         ↑
   credential
   (encrypted)
```

1. User initiates action in Odoo
2. Odoo retrieves the encrypted credential
3. Credential decrypted in memory
4. Call made to the provider
5. Response processed in Odoo

Nothing goes to Pantalytics.

## Audit Trail

All email operations are logged:

- Sent emails indexed by the Message-ID the provider put on the wire
- Sending logged with the `[Outgoing Mail]` tag, sync with `[Incoming Mail]`
- Provider API errors logged with `[Graph API]`, `[Gmail API]`, `[IMAP]` or `[SMTP]`

## Compliance

- **GDPR:** User data processed per the provider's data processing terms
- **Data residency:** Determined by the provider's tenant configuration
- **Odoo data:** Stored in your Odoo database location
