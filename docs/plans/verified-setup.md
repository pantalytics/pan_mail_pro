# A setup the module can verify

Status: proposal, 2026-10-07. Nothing built. Supersedes the "find out at the
send" posture in ARCHITECTURE.md §2 *Connected as* and closes #266, #271 and
#295 when it lands.

## 1. What went wrong, in one case

Emovr, 2026-10-05 to 2026-10-07. Seventeen mails in `exception`, offertes
among them, every one with the same raw Graph line in `failure_reason`:
`404 ... /users/info@emovr.nl/messages ... The specified object was not found
in the store`. Every mailbox reported **healthy**. The setup checklist was
green. The owner found out from a customer who had not received a quotation.

What the database knew and did not put together:

| Fact | Where it sat | What it meant |
|------|--------------|---------------|
| `info@emovr.nl` is owned by Robert, whose sign-in is `robert@stalero.nl` | `pan.mail.mailbox.owner_user_id` | `mailbox_type = shared`, so every sender's own token is used |
| Robert's token has no Full Access on `info@emovr.nl` | nowhere; Exchange refused it 17 times | every send by Robert fails |
| `info@emovr.nl` is a licensed **user**, not a shared mailbox | nowhere; two people had signed in *as* it | a user mailbox normally sends with its own sign-in, and nobody is given Full Access on it |
| Daniëlle is connected as `info@emovr.nl`, not as her own address | `pan.mail.account.email`, shown as *Connected as* | her sends from `info@` work, from `anna@stalero.nl` they don't |
| `info@ticomit.nl` has failed 2116 syncs with `ErrorInvalidUser` | `sync_failure_count` | the address is not a mailbox at all (an alias, a group, or gone) |

None of this is unknowable. Most of it is a GET away with the token already in
the database. The module stores the one thing the OAuth callback hands it (an
address) and infers the rest from Odoo-side fields, then waits for a send to
fail to learn the truth, and the failure lands as a raw Graph line on a mail
nobody opens.

§2 *Connected as* records the earlier decision: a check button was built and
removed because "a person who cannot send from a shared mailbox finds out at
the send". Emovr is the case where that is wrong. The person who finds out is
the customer, and the person who can fix it (an Exchange administrator) is
neither of them.

## 2. What Microsoft will and will not tell us

Checked against the Graph docs, 2026-10-07.

**It will say what a mailbox is.** `GET /users/{address}/mailboxSettings/userPurpose`
returns `user`, `shared`, `room`, `equipment`, `linked` or `others`. Scope:
`MailboxSettings.Read`, delegated, no admin consent. Reading another mailbox's
settings works only with rights on that mailbox, which is the second question
anyway.

**It will say whether an identity can read a mailbox.** Any
`GET /users/{address}/mailFolders/inbox` with an identity's token is a Full
Access probe: 200 or a refusal. One call, no side effect.

**It will not say whether an identity may send as a mailbox.** Verbatim:
"It's not currently possible to use Microsoft Graph to query which mailboxes
the authenticated user has permissions for." Send As is only ever proven by a
send. `ErrorSendAsDenied` on the send is the refusal.

**It will say who signed in.** `/me` returns `id`, `userPrincipalName`, `mail`,
`displayName`; the id_token carries `tid` (tenant) and `oid`. The callback
fetches `/me` today and keeps only the address.

**It will resolve a directory user by UPN.** `GET /users/{address}` returns the
user's primary `mail` and `userPrincipalName`. Scope `User.ReadBasic.All`,
delegated, no admin consent. It does not resolve aliases or groups, which is
exactly how an alias is told apart from a mailbox: Exchange endpoints resolve
any proxy address, the directory endpoint resolves only principals.

What we deliberately do not ask for: `User.Read.All`, `Group.Read.All`,
`MailboxSettings.Read` as an application permission. All three need admin
consent, and the cost of that at a 10-person customer is a week of waiting on
whoever has the Global Admin login. Without them we cannot name the mailbox an
alias belongs to, nor tell a distribution list from a typo. Both come out as
"no mailbox of its own at this address", which is the sentence the admin needs.

## 3. Design

Three things, in the order they are needed. Each is a model or a table, each
has one owner, and the screens only read.

### 3.1 The identity is a record, not an address

`pan.mail.account` grows what the callback already receives and discards:

| Field | Source | Why |
|-------|--------|-----|
| `provider_user_id` | `/me.id` (= id_token `oid`) | the stable key; addresses get renamed |
| `principal_name` | `/me.userPrincipalName` | what the admin sees in Entra; often ≠ `mail` |
| `display_name` | `/me.displayName` | "Connected as Robert van Laar, robert@stalero.nl" |
| `tenant_id` | id_token `tid` | refuses a sign-in from the wrong tenant at the callback, not at the first send |
| `granted_scopes` | token response `scope` | a probe that needs a scope the grant predates says "reconnect", not "denied" |
| `connected_date` | the callback | when this grant was made |
| `verified_date` | the last probe that succeeded | the identity's own heartbeat |

Rules that follow:

- **Tenant.** `tid` must equal `pan.mail.provider.tenant_id` unless the
  registration is multi-tenant. A mismatch is a refusal at the callback with
  the two tenants named, and an `oauth.tenant_mismatch` row.
- **Identity vs user.** `account.email` ≠ `user.email` is **not** refused.
  Sharing a sign-in is how Emovr's Daniëlle works, and *Connected as* already
  shows it. It becomes an answer, not a rule: the Users list column reads
  "robert@stalero.nl" under a user whose email is "robert@emovr.nl", and that
  is the whole warning. Dropped case: enforcing a match. It would lock out
  every customer whose Odoo login is not their mailbox.
- **Gmail** stores `sub`, `email`, `name` from userinfo into the same fields.
  IMAP stores nothing new; its identity is its login.

`_identity_label` reads `display_name` instead of calling `/me`.

### 3.2 What a mailbox is, asked of the provider

`pan.mail.mailbox.address_kind`, stored, set by verification, never typed:

| Kind | Found by | What it means for sending |
|------|----------|---------------------------|
| `user` | `userPurpose = user` or `linked` | a person's mailbox; it sends with its own sign-in, and giving someone else Full Access on it is unusual |
| `shared` | `userPurpose = shared` | a shared mailbox; every sender needs Full Access + Send As |
| `resource` | `room`, `equipment`, `others` | not for mail; refused |
| `alias` | Exchange resolves it, the directory does not | an extra address on some other mailbox; sending from it needs rights on that mailbox, which we cannot name |
| `none` | Exchange: `ErrorInvalidUser` / not found | no mailbox at this address: a group, a list, a typo, or deleted |
| `unknown` | not probed yet, or the token lacks the scope | the state every existing row starts in |

`mailbox_type` (personal / shared) stays. It is Odoo's policy question (who
may send from here, with whose credentials) and `address_kind` is Exchange's
factual one. They are read together:

- `user` kind + owner connected as that address → the ordinary personal mailbox.
- `user` kind + owner connected as **another** address → the Emovr shape. It
  works only with Full Access + Send As on a user mailbox, which admins rarely
  grant. The status line says so: "info@emovr.nl is a user account. Connect
  it as its own sign-in, or grant robert@stalero.nl Full Access and Send As on
  it."
- `shared` kind + no owner on Gmail/IMAP → fine, its own account.
- `alias`, `none`, `resource` → the mailbox is broken whatever else is true.

The provider contract gets one method, `inspect_mailbox(account, address)`,
returning `{'kind', 'can_read', 'error'}` in normalized form. Microsoft
implements the ladder in §4; Gmail and IMAP return `kind='user'` and
`can_read` from a cheap list call, because there an address is its own
account and nothing else is possible. The boundary grep stays unbroken.

### 3.3 Access is a table

`pan.mail.mailbox.access`: one row per (mailbox, account), written only by
verification and by the send path.

| Field | Written by |
|-------|------------|
| `can_read` (yes / no / unknown) | the inbox probe |
| `can_send` (yes / no / unknown) | the outcome of the last send with this pair: `ErrorSendAsDenied` or the draft refusal sets `no`; a delivered mail sets `yes` |
| `checked_date`, `error` | whichever wrote last |

Which pairs exist is `resolve_sending_account` and `resolve_receiving_account`
run in reverse: for each mailbox, the owner's account (reads and, if personal
or notification, sends) and every account that has sent through it. Shared
mailboxes on Microsoft get a row per sender as senders appear; no row means
"this person has not tried yet", which is honest.

The table is the one place "does it work" is answered. `health_status`,
`status_message`, the setup checklist and the heartbeat all read it; nothing
else contacts the provider to find out.

**The notification mailbox** is the row (notification mailbox, owner's
account). Step 4 of the checklist goes green only when `can_read = yes` and
`can_send = yes` on that row. `can_send` gets its first `yes` from the test
email the step already offers, or from the first real system mail. Until then
the step reads "notifications@emovr.nl, not yet sent from", which is open,
not broken.

## 4. The probe ladder (Microsoft)

Run with one account's token against one address. Each rung answers one
question and stops the ladder where the answer makes the rest moot.

| # | Call | 200 | Refusal | Writes |
|---|------|-----|---------|--------|
| 1 | token refresh | continue | `invalid_grant` → `connected = False`, stop | account `verified_date` |
| 2 | `GET /users/{addr}/mailFolders/inbox?$select=id` | `can_read = yes` | `ErrorAccessDenied` / `ErrorItemNotFound` / 404 → `can_read = no`; `ErrorInvalidUser` → `kind = none`, stop | access row |
| 3 | `GET /users/{addr}/mailboxSettings/userPurpose` | `kind` from value | insufficient scope → `kind = unknown`, error "reconnect to let Mail Pro check this mailbox"; access denied → leave `kind` | mailbox |
| 4 | `GET /users/{addr}?$select=mail,userPrincipalName` | `addr` ≠ `mail` → note the primary; else nothing | 404 and rung 2 passed → `kind = alias` | mailbox |

Rung 2 before 3 on purpose: a read probe needs no new scope, so an existing
grant answers the question that matters (can this identity work here) before
being asked to reconnect for the one that explains it.

**Step 0 of the build is the probe matrix on our own tenant**: a user mailbox,
a shared mailbox, an alias on each, a distribution list, a Microsoft 365
group, a deleted address, with and without Full Access, with and without the
new scopes. The exact `code` strings Exchange returns for each cell go into
`docs/research/graph-probes.md` and become the test fixtures. The docs do not
list them, Emovr's `404 ... not found in the store` is not in
`_DELEGATION_ERRORS` today, and a ladder built on guessed codes is the bug
again with better wording.

## 5. When verification runs

Never in a constraint and never on opening a form; both would put an HTTP call
where the ORM expects none. Three moments:

1. **The OAuth callback.** After `_store_tokens`, the identity is verified
   (rung 1 is implicit) and every mailbox this account owns or has sent
   through gets rungs 2 to 4. The person who just consented sees the result on
   the same My Preferences tab.
2. **The sync cron, once an hour per mailbox.** The fetcher already holds the
   owner's token and already reads the inbox, so rung 2 is free and rungs 3 to
   4 are two GETs. `last_check_date` keeps meaning what it means; the probe
   result is beside it, not in it.
3. **Check access on the mailbox form**, a secondary button on the Setup tab,
   and the existing *Try again* in the red alert, which runs the ladder before
   the sync. One primary action per screen stays *Send test email*.

The send path writes `can_send` on every outcome; it does not probe.

## 6. What the screens say

Per DESIGN_SYSTEM.md: the checklist is the status, one line per fact, a fix
line only while it is a fix, no banner, no tooltip, silence when it works.

**Setup checklist, step 4.** Today: "Notification mailbox · notifications@emovr.nl".
Same line, and the dot reads the access row. Red with one sentence under the
name while broken:

> Robert van Laar's sign-in (robert@stalero.nl) cannot send from
> notifications@emovr.nl. An administrator grants Full Access and Send As on
> that address in the Exchange admin center.

**Mailbox list.** The health badge gains the new reasons and nothing else. An
optional column *Kind* (user / shared / alias / none), hidden by default.

**Mailbox form.** `sends_with` becomes true instead of structural: "Sends with
Robert van Laar's sign-in, robert@stalero.nl" gets " · cannot read this
mailbox" or " · last sent 7 Oct" after it. `status_message` carries the
sentence from §3.2 when the kind and the owner disagree. Shared mailbox on
Microsoft: a muted line per sender who has tried, "Daniëlle, info@emovr.nl ·
sends", "Robert van Laar, robert@stalero.nl · refused on 7 Oct". That list is
the one thing the old check button could not show and the admin needs.

**Users list.** Unchanged: *Connected as* already shows a sign-in that is not
the user's own. What changes is the user form's Connected account group: the
display name joins the address, and "Verified 7 Oct" or "Could not verify:
reconnect" under it, muted.

**Failed mail.** `failure_reason` never carries a raw Graph line again. The
draft-creation 404 joins `_DELEGATION_ERRORS` (exact code from step 0), so it
gets the same sentence as `ErrorSendAsDenied`: who, which address, which two
rights. A customer-facing bounce has no `/users/...` URL in it.

**Technical.** Settings → Technical → Email → Mail Pro → Access: the table,
list view, system-only like Accounts. Mailbox, sign-in, read, send, checked,
error. Group by mailbox. That is the debug screen; the mailbox form shows its
conclusion, not its rows.

## 7. Debugging and the heartbeat

New codes in `pan.mail.error.CODES`, flow `access`:

- `access.read_denied`, `access.send_denied`, `access.no_mailbox`,
  `access.scope_missing`, `oauth.tenant_mismatch`.

Each is one `_record()` at the rung that found it, with `mailbox` and
`account`, so the ledger row names the pair. The heartbeat already carries
`codes_since()`; it adds `access_ok` (no mailbox with a `no` on a row it
needs) beside `sync_ok`, so Pantalytics sees "Emovr: 1 mailbox nobody can send
from" the day it happens rather than six months later (#266, #271).

`[Access]` is the log tag.

## 8. What is dropped

- **Rerouting to another identity** (#295). A send goes out with the sign-in
  the route named or not at all; §9.5 holds. Picking a working token silently
  is how a mail leaves from an address its author did not choose.
- **Enforcing account email = user email.** Shown, never refused (§3.1).
- **Admin-consent scopes.** No naming the mailbox behind an alias, no
  distinguishing a group from a typo. Both say "no mailbox of its own here".
- **Probing Send As without sending.** Impossible; the test email is the probe.
- **Send on Behalf.** Exchange accepts it with the `from` header the module
  already sets; the recipient sees "on behalf of". Not detected, not
  documented as supported.
- **Verifying from a constraint or at form load.** No network in either.

## 9. Build order

Each step ships on its own, bumps the manifest, and passes
`BASE_REF=origin/19.0 tools/ci_lint.sh` and the suite.

| Step | What | Done when |
|------|------|-----------|
| 0 | Probe matrix on the Pantalytics tenant, `docs/research/graph-probes.md` | every cell of §4 has its real `code` |
| 1 | Identity: scopes `MailboxSettings.Read` + `User.ReadBasic.All`, id_token claims, the §3.1 fields, tenant refusal, Gmail's userinfo. `tests/test_identity.py` | the callback stores six fields and refuses a foreign tenant |
| 2 | `inspect_mailbox` on the contract, the Microsoft ladder, `address_kind`, `pan.mail.mailbox.access`, the three run moments. `tests/test_mailbox_access.py` on the step-0 fixtures | a fake Exchange that refuses Full Access produces a `no` row and an `access.read_denied` ledger row |
| 3 | Health, status, step 4, heartbeat and `failure_reason` read the table. The 404 draft error mapped. Extend `test_microsoft_provider.py`, `test_connected_as.py` | Emovr's shape (user kind, owner on another address) renders the §3.2 sentence; `health_status = error` |
| 4 | Screens: the form lines, the Access technical list, the Kind column. `tools/ui_check.py`: step 4 red line text, the per-sender list on a shared mailbox | the browser check reads both |
| 5 | ARCHITECTURE.md §2 (replace *Connected as* and the `userPurpose` rejection), §13 (the limitation narrows to Send As), `docs/troubleshooting.md`, README scopes. This file leaves `docs/plans/` | CI's model-in-ARCHITECTURE check passes |

Migration: `19.0.28.0.0` adds columns only. `address_kind` starts at
`unknown` on every row and the first cron hour fills it. No backfill from
guesses.
