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

Every Gmail call is `users/me/...` with the sign-in's own token. Checked
against Google's reference on 20 Nov 2026; why Google has no shared type is in
`shared-mailboxes.md`.

### Identity

| Source | Field | Status |
|--------|-------|--------|
| id_token (`openid email`) | `sub`: "unique among all Google Accounts and never reused"; the identifier to key on, never `email`, which "could change over time" | documented (OpenID Connect) |
| id_token | `hd`: "Provided only if the user belongs to a Google Cloud organization"; absent means not a hosted domain. Trusted from the token, never from the `hd` request hint | documented. Which domain a secondary-domain user carries: unverified |
| id_token signature | not checked: it comes straight from the token endpoint over TLS, which Google says is enough. Passing it on to other components would need the check | documented |
| `users/me/profile` | `emailAddress`, `messagesTotal`, `threadsTotal`, `historyId`. No type, no delegates | documented, observed (`read_user_info`) |
| refresh token | only with `access_type=offline`, and only on the first code exchange; `prompt=consent` forces that exchange to count as first | documented (offline); `prompt=consent` is the module's reading |

### What the address is, and whether this sign-in may send as it

`users/me/settings/sendAs`, covered by `gmail.modify` (documented:
`sendAs.list` accepts `gmail.modify`, `gmail.readonly`, `gmail.settings.basic`
or `mail.google.com`).

| Entry for the address | `address_kind` | `can_send` | Status |
|-----------------------|----------------|------------|--------|
| `isPrimary: true` ("the primary address used to login to the account") | `user` | yes | documented. Also true for every shared address: Google has no other type |
| `verificationStatus: accepted`, not primary | `alias` | yes | documented ("Aliases that don't require verification have a verification status of accepted") |
| `verificationStatus: pending` | `alias` | no | documented |
| absent | `none` | no | by construction |
| a user alias the Workspace admin added | ? | ? | **unverified**: Google does not say whether it is in the list without the user adding it |

Can read: the profile call, on the account's own address only. Google's
reference accepts any `userId` and does not say what another address returns;
community reports agree a delegate's token sees its own mailbox only, and the
only documented route to another mailbox is a service account with
domain-wide delegation. **unverified on a tenant.**

What else the API could say, and does not use: Admin SDK `users.get` with
`viewType=domain_public` works for a non-admin (scope
`admin.directory.user.readonly`, contact sharing on) and accepts an alias as
`userKey`, so it tells primary from alias. The User resource has no field for
a shared inbox; `isMailboxSetup` is the only mailbox field. `settings.delegates`
is "only available to service account clients that have been delegated
domain-wide authority".

### Sending

| Case | What happens | Status |
|------|--------------|--------|
| `From:` is the account's primary or an accepted send-as | sent as that address | documented (`sendAsEmail`: "The email address that appears in the From: header") |
| `From:` is not in the send-as list | community: Gmail rewrites `From:` to the primary and sends, no error | **unverified**, and not documented. This is how Dorset's system mail went out as nikki@ before 19.0.40.2.2 |
| 403 "Delegation denied for <address>" | community: a `userId` other than the token's own address. The module always calls `users/me`, so `_delegation_denied_reason` should never fire | **not documented** (absent from the error reference); earlier marked documented here, wrong |

The module's guard against the rewrite is upstream: since 19.0.40.2.2 a Gmail
mailbox sends only with the account whose address it is
(`resolve_receiving_account`), so `From:` is always that account's primary.

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
fixtures updated where a reading above was wrong.

The Workspace tenant is on a domain of its own, not pantalytics.com: that
domain's MX is Microsoft's, so a Gmail inbox there would never receive mail
from outside. Business Starter or higher (the native Shared inbox is Business
editions only), its own OAuth client as an Internal app, three users: two
people and one shared address. Decided 20 Nov: the Provisional Bureau demo brand
(`provisional-bureau.cloudpepper.site` runs Mail Pro with CRM and Sales), so
the tenant is the Google demo as well as the test. Not Bean Forge: a real
coffee roaster trades as The Bean Forge (thebeanforge.com), and a live mail
domain under that name in the same trade invites confusion.

On that tenant, through the OAuth Playground with our own client id
(its redirect URI registered; Google's own client revokes refresh tokens
after 24h): a pending and an accepted send-as alias; an admin-added user
alias in `sendAs.list`; `messages.list` with `userId` set to a mailbox the
signed-in user is a delegate of; `messages.send` with a `From:` outside the
send-as list, then the `From:` of the copy in Sent; `hd` of a user on a
secondary domain; and a native Shared inbox (Admin console, Users): can it
sign in, does it list as a user.
