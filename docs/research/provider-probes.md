# What each provider answers to a probe

The response matrix behind `inspect_mailbox()` (ARCHITECTURE §2, the plan in
`docs/plans/verified-setup.md` §4 and §8). One row per question the module
asks, the call it makes, and what came back.

**Status of each cell.** `documented` is the vendor's own reference;
`observed` is a response seen on a real tenant, with where; `unverified` is
the module's reading and still to be confirmed against a tenant. A ladder
built on a guessed code is the Emovr bug with better wording, so every
`unverified` cell is a task, not a fact. The test fixtures in
`tests/test_mailbox_access.py` carry the same strings as this file.

## Microsoft 365 (Graph, delegated token)

Every call below is `GET`, with the sign-in's own token, against
`https://graph.microsoft.com/v1.0`.

### Rung 1: the token

| Response | Meaning | Status |
|----------|---------|--------|
| token refreshed | identity still works | observed, every customer |
| `invalid_grant` | consent withdrawn, password changed, token expired | observed (`refresh_access_token`) |

### Rung 2: can this sign-in read the mailbox

`/users/{address}/mailFolders/inbox?$select=id`

| `error.code` | HTTP | Meaning | Status |
|--------------|------|---------|--------|
| (none) | 200 | Full Access, or the sign-in's own mailbox | observed |
| `ErrorAccessDenied` | 403 | a mailbox exists; this sign-in may not read it | documented (outlook-share-messages-folders: "return an error"); the code is what the send path has seen on the draft |
| `ErrorItemNotFound` | 404 | "The specified object was not found in the store": the same refusal, phrased by the store rather than by the permission check | observed at Emovr 2026-10-05 on `POST /users/info@emovr.nl/messages` with robert@stalero.nl's token: 17 mails. The message text is confirmed; that the code is `ErrorItemNotFound` is the module's reading of the message and is **unverified** |
| `ErrorInvalidUser` | 404 | "The requested user 'x' is invalid": no mailbox at this address | observed at Emovr on `info@ticomit.nl`, 2116 sync failures (`fetch_messages`, same shape of URL) |
| `MailboxNotEnabledForRESTAPI` | 404 | an on-premises or unlicensed mailbox | documented (Graph known issues); unverified |
| `ResourceNotFound` | 404 | the directory object does not exist | unverified |

### Rung 3: what the mailbox is

`/users/{address}/mailboxSettings/userPurpose`, scope `MailboxSettings.Read`.

| Response | `address_kind` | Status |
|----------|----------------|--------|
| `{"value": "user"}` | `user` | documented (`mailboxSettings` resource) |
| `{"value": "linked"}` | `user` | documented |
| `{"value": "shared"}` | `shared` | documented |
| `room`, `equipment`, `others` | `resource` | documented |
| the grant lacks `MailboxSettings.Read` | `unknown`, no sentence | by construction: the module reads `granted_scopes` and does not make the call. The scope is requested only when `pan_mail_pro.graph_inspect_scopes` is set |
| 403 `ErrorAccessDenied` with the scope held | the rung-2 refusal again; `kind` is left as it was | unverified |

### Rung 4: is the address a principal or an alias

`/users/{address}?$select=mail,userPrincipalName`, scope `User.ReadBasic.All`.

| Response | Meaning | Status |
|----------|---------|--------|
| 200, `mail` equals the address | the address is this mailbox's primary | documented (user-get: resolves by `id` or `userPrincipalName`) |
| 200, `mail` differs | the address is a UPN whose primary SMTP is something else; the mailbox is still its own | unverified how often this occurs |
| 404 `Request_ResourceNotFound`, and rung 2 passed | Exchange resolves the address (a proxy address) but the directory does not: an alias of some other mailbox | unverified: that the directory refuses a proxy address is documented, the exact code is not |
| 404, and rung 2 said `ErrorInvalidUser` | no mailbox and no principal: a group, a distribution list, a typo | unverified |

### Sending

| `error.code` | When | Status |
|--------------|------|--------|
| `ErrorSendAsDenied` | `POST .../send`, no Send As and no Send on Behalf | documented (outlook-send-mail-from-other-user, example 2) |
| `ErrorAccessDenied` | `POST /users/{address}/messages`, no Full Access | observed (`_DELEGATION_ERRORS`, 19.0.25) |
| `ErrorItemNotFound` | same draft call, same cause, phrased by the store | observed at Emovr (see rung 2) |

Verbatim from the docs: "It's not currently possible to use Microsoft Graph
to query which mailboxes the authenticated user has permissions for." A send
is the only proof of Send As.

## Google Workspace (Gmail API)

### Identity

| Source | Field | Status |
|--------|-------|--------|
| id_token (`openid email`) | `sub`, `email`, `hd` | documented (OpenID Connect; `hd` present only on Workspace accounts) |
| `users/me/profile` | `emailAddress` | observed (`read_user_info`) |

### What the address is, and whether this sign-in may send as it

`users/me/settings/sendAs`, covered by `gmail.modify` (documented:
`sendAs.list` accepts `gmail.modify`, `gmail.readonly`, `gmail.settings.basic`
or `mail.google.com`).

| Entry for the address | `address_kind` | `can_send` | Status |
|-----------------------|----------------|------------|--------|
| `isPrimary: true` | `user` | yes | documented |
| `verificationStatus: accepted`, not primary | `alias` | yes | documented |
| `verificationStatus: pending` | `alias` | no | documented |
| absent | `none` | no | by construction |

Can read: the profile call, on the account's own address only. The Gmail API
does not expose another user's mailbox to a delegate's OAuth token, so a
mailbox is read by its own sign-in or not at all.

### Sending

| Error | When | Status |
|-------|------|--------|
| 403 "Delegation denied for <address>" | `From:` not in the account's send-as list | documented in Gmail API error reference; unverified on a Workspace tenant |

## IMAP/SMTP

| Question | Call | Answer | Status |
|----------|------|--------|--------|
| identity | login | the login; nothing else exists | by construction |
| kind | none | `account` | by construction |
| can read | `SELECT INBOX` read-only | `OK`, or an `IMAP4.error` | observed (GreenMail in CI, `test_connection`) |
| can send | `MAIL FROM:<address>` then `RSET` | `250` proves nothing; `5xx` is a refusal with the server's own line | GreenMail: `250` to any address (observed in CI). Exchange Online SMTP AUTH: `550 5.7.60 SMTP; Client does not have permissions to send as this sender` on a `MAIL FROM` the login may not use -- documented in Exchange's NDR codes, unverified from this module |

## The step-0 matrix still to run

On the Pantalytics tenant, one sign-in with and one without Full Access,
against: a user mailbox, a shared mailbox, an alias of each, a distribution
list, a Microsoft 365 group, a deleted address. Each cell's `error.code` and
`message` into the tables above, and `tests/test_mailbox_access.py`'s
fixtures updated where a reading above was wrong. On a Workspace account: a
pending and an accepted send-as alias.
