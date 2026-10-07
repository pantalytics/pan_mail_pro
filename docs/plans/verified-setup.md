# A setup the module can verify

Status: **built**, 19.0.28.0.0, except the probe matrix on a real tenant
(step 0, §10). 19.0.28.1.0 made the two Graph inspection scopes opt-in
(`pan_mail_pro.graph_inspect_scopes`): rungs 3 and 4 are silent without them,
and no customer has to touch Azure or reconnect for the check that matters.
`docs/research/provider-probes.md` names every cell that is still the module's reading rather than an observed response, and the
fixtures in `tests/test_mailbox_access.py` carry the same strings. The design
has moved into ARCHITECTURE.md §2 *Connected as* and *Verified access*; this
file stays for the reasoning until that matrix has run, then leaves.

Four things landed differently from the plan below, each for a reason:

- **The phase does not wait for the verified answer.** §3.3 said step 4 goes
  green only after a read probe and a send, and it does; but `is_ready()`
  keeps reading the credentials, because a phase that waited would stop every
  existing database's incoming sync at the upgrade until both had happened.
- **No `access_ok` on the heartbeat** (§7). mail-pro-admin refuses a field it
  does not know, and a refused heartbeat at every customer is worse than a
  missing boolean. The `access.*` codes ride the error list it accepts; the
  boolean follows once the server takes one.
- **No "Verified 7 Oct" line on the user form** (§6). The account form has
  the date and the Users list already shows the sign-in; a third place to say
  it is a line nobody asked for.
- **`tools/ui_check.py` is unchanged.** The new menu is walked by the check
  that opens every menu; the new sentences are asserted in
  `tests/test_mailbox_health.py` rather than in the browser, since no browser
  ran in the session that built this.

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
- **Gmail and IMAP/SMTP** fill the same fields from what they have; §8.

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
returning `{'kind', 'can_read', 'can_send', 'error'}` in normalized form.
Microsoft implements the ladder in §4 and answers `can_send = unknown`;
Google and IMAP/SMTP answer it their own way, §8. The boundary grep stays
unbroken: nothing outside a provider directory names an endpoint.

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
  `oauth.tenant_mismatch`.

Each is one `_record()` at the rung that found it, with `mailbox` and
`account`, so the ledger row names the pair. The heartbeat already carries
`codes_since()`; it adds `access_ok` (no mailbox with a `no` on a row it
needs) beside `sync_ok`, so Pantalytics sees "Emovr: 1 mailbox nobody can send
from" the day it happens rather than six months later (#266, #271).

`[Access]` is the log tag.

## 8. Google Workspace and IMAP/SMTP

The three providers answer the same three questions (who signed in, what is
this address, may this sign-in read and send from it) with different
evidence. The table, the health rungs and the screens are provider-neutral;
only the probes differ. ARCHITECTURE §1 *Capability differences* gets the
three rows below.

| Question | Microsoft 365 | Google Workspace | IMAP/SMTP |
|----------|---------------|------------------|-----------|
| Identity | `/me` + id_token (`oid`, `tid`) | id_token (`sub`, `email`, `hd`) + Gmail profile | the login; nothing else exists |
| Tenant | `tid` = app registration's tenant | `hd` = the Workspace domain the admin entered | none |
| Kind | `userPurpose` + directory lookup | primary, or a verified send-as alias, or nothing | `account`: an IMAP login *is* a mailbox |
| Can read | inbox probe with each sign-in | the account's own profile call | `SELECT INBOX` read-only |
| Can send | only a send proves it | **`sendAs.list` proves it before a send** | only a send proves it |

### 8.1 Google Workspace

**Identity.** The callback asks for `openid email` already, so the id_token
carries `sub` (stable id), `email` and `hd`, the hosted domain. `sub` fills
`provider_user_id`, `hd` fills `tenant_id`. A consumer `@gmail.com` sign-in
has no `hd` and is refused at the callback the way a foreign Microsoft tenant
is: Mail Pro is a Workspace product and a personal Gmail cannot hold a shared
address. `display_name` stays empty; the Gmail profile has none and
`userinfo.profile` is a scope we do not add for a name.

**Kind and Send As, in one call.** Gmail's
`users.settings.sendAs.list` returns every address this account may put in
`From:`, each with `isPrimary` and `verificationStatus`. It is covered by
`gmail.modify`, which the module already holds, so no new consent. For a
mailbox address and the account that serves it:

| `sendAs.list` says | `address_kind` | `can_send` |
|--------------------|----------------|------------|
| `isPrimary` and `sendAsEmail` = address | `user` | yes |
| `verificationStatus = accepted`, not primary | `alias` | yes |
| `verificationStatus = pending` | `alias` | no: "awaiting verification in Gmail settings" |
| absent | `none` for this sign-in | no: "this sign-in has no send-as address for X" |

This is the one provider where Send As is a lookup rather than a guess, and
it answers the Gmail shape of the Emovr mistake: an admin files
`sales@customer.com` as a shared mailbox, signs in as themselves instead of
as `sales@`, and every send would fail with Gmail's `Delegation denied`. The
row says so the moment the mailbox is saved and the hour's probe runs.

A Google Group with a collaborative inbox is `none` unless somebody has
added it as a send-as alias on the account; the Directory and Groups APIs
need admin scopes, same decision as §2. Gmail delegation (one user reading
another's mailbox) is invisible to the Gmail API with the delegate's OAuth
token, so a delegated mailbox cannot be served by its delegate and the
module does not pretend otherwise: a shared mailbox on Google is a sign-in
as that address, which is what `resolve_receiving_account` already says.

**Can read** is the profile call the sync already makes. One probe per
account, not per (mailbox, account): on Google there is exactly one sign-in
that can read a given mailbox, its own.

### 8.2 IMAP/SMTP

**Identity.** A login and two hosts, typed by an administrator. There is
no identity endpoint and nothing to verify the address against, so
`provider_user_id`, `tenant_id` and `display_name` stay empty and the
account's `email` is taken on faith. `verified_date` still moves on every
successful probe.

**Kind** is `account`: a login that opens a mailbox is a mailbox. Whether
the address is somebody's alias or a group on the server is not knowable
over IMAP, and the hard call is not to try: the one failure this hides (the
admin typed `info@` but the login is `robert@`, so the sync reads Robert's
inbox under the wrong label) is caught by the send, below.

**Can read** is `SELECT INBOX` read-only, which `test_connection` does
today and the sync does every minute. Free.

**Can send.** SMTP has no Send As to query. `MAIL FROM` is sometimes
refused on the spot (Microsoft and Google relays check it; most others say
250 and bounce later), so a cheap probe is an envelope `MAIL FROM:<address>`
followed by `RSET`: a 5xx sets `can_send = no` with the server's own line, a
250 leaves it `unknown` until a delivered mail sets `yes`. That asymmetry is
the truth of the protocol and the row shows it as such. The step-0 matrix
gets an SMTP column: our GreenMail says 250 to anything, Exchange Online's
SMTP AUTH relay refuses a `MAIL FROM` the login may not send as, and both
answers go into the fixtures.

**Sent copy.** The APPEND to Sent that ARCHITECTURE §9.6 does best-effort is a third
right (write to a folder) and its failure already lands in
`outgoing.sent_copy_failed`. It stays out of the access table: the mail was
delivered, and a row that reads "no" over a missing Sent folder would send
an admin to fix the wrong thing.

### 8.3 What the shared code does with the differences

`health_status` reads `can_read` and `can_send` and never asks which
provider wrote them. The one provider-specific sentence is the fix line,
which each client already owns (`_no_credentials_error` has one per
provider today): Exchange says "Full Access and Send As", Gmail says "add
the address under Send mail as in Gmail settings, or sign in as it", SMTP
quotes the server's refusal.

Step 4 of the checklist asks the same of the notification mailbox on every
provider: `can_read = yes` and `can_send = yes` on the owner's row. On
Google that is green after the hour's probe without anybody sending; on
Microsoft and SMTP after the test email. The step's open-state line says
which: "not yet sent from" only appears where a send is the only proof.

## 9. What is dropped

- **Rerouting to another identity** (#295). A send goes out with the sign-in
  the route named or not at all; §9.5 holds. Picking a working token silently
  is how a mail leaves from an address its author did not choose.
- **Enforcing account email = user email.** Shown, never refused (§3.1).
- **Admin-consent scopes.** No naming the mailbox behind an alias, no
  distinguishing a group from a typo. Both say "no mailbox of its own here".
- **Probing Send As without sending** on Microsoft and SMTP. Impossible on
  the first, a 250 that means nothing on the second; the test email is the
  probe. Google is the exception and gets the lookup (§8.1).
- **Send on Behalf.** Exchange accepts it with the `from` header the module
  already sets; the recipient sees "on behalf of". Not detected, not
  documented as supported.
- **Verifying from a constraint or at form load.** No network in either.

## 10. Build order

Each step ships on its own, bumps the manifest, and passes
`BASE_REF=origin/19.0 tools/ci_lint.sh` and the suite.

| Step | What | Done when |
|------|------|-----------|
| 0 | Probe matrix: Graph on the Pantalytics tenant, `sendAs.list` on a Workspace account with a pending and an accepted alias, `MAIL FROM` on GreenMail and on Exchange Online's SMTP relay. `docs/research/provider-probes.md` | every cell of §4 and §8 has its real response |
| 1 | Identity: scopes `MailboxSettings.Read` + `User.ReadBasic.All`, id_token claims on both OAuth providers, the §3.1 fields, tenant / `hd` refusal. `tests/test_identity.py` | the callback stores six fields and refuses a foreign tenant and a consumer Gmail |
| 2 | `inspect_mailbox` on the contract, the Microsoft ladder, Gmail's `sendAs.list`, the SMTP envelope probe, `address_kind`, `pan.mail.mailbox.access`, the three run moments. `tests/test_mailbox_access.py` on the step-0 fixtures, one class per provider, and `test_provider_contract.py` asserting all three implement it | a fake Exchange that refuses Full Access produces a `no` row and an `access.read_denied` ledger row; a fake Gmail with a pending alias produces `can_send = no` |
| 3 | Health, status, step 4, heartbeat and `failure_reason` read the table. The 404 draft error mapped. Extend `test_microsoft_provider.py`, `test_connected_as.py` | Emovr's shape (user kind, owner on another address) renders the §3.2 sentence; `health_status = error` |
| 4 | Screens: the form lines, the Access technical list, the Kind column. `tools/ui_check.py`: step 4 red line text, the per-sender list on a shared mailbox | the browser check reads both |
| 5 | ARCHITECTURE.md §1 capability table (three rows), §2 (replace *Connected as* and the `userPurpose` rejection), §13 (the limitation narrows to Send As on Microsoft and SMTP), `docs/troubleshooting.md` per provider, README scopes. This file leaves `docs/plans/` | CI's model-in-ARCHITECTURE check passes |

Migration: `19.0.28.0.0` adds columns only. `address_kind` starts at
`unknown` on every row and the first cron hour fills it. No backfill from
guesses.
