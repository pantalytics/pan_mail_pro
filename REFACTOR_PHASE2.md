# Phase 2: Provider and account models

Working checklist for Phase 2 of [REFACTOR_PLAN.md](REFACTOR_PLAN.md). Phase 1 is
[REFACTOR_PHASE1.md](REFACTOR_PHASE1.md).

**Goal:** make "which provider" and "whose credentials" first-class data instead of hardcoded
Microsoft assumptions, so a second provider has somewhere to live.

**This is the highest-risk phase in the project.** Phase 1 moved code; this moves *data*. A botched
token migration disconnects every user in production, and the tokens are Fernet-encrypted, so a
half-done migration is not obviously wrong by inspection — it fails later, at send time.

**Do not deploy this before Phase 1's manual checks pass.** Building it on a branch is free; shipping
an unverified refactor plus a data migration together is not.

---

## One model, not two — a correction to the plan

REFACTOR_PLAN.md §3 specifies **two** new models, `pan.mail.provider` and `pan.mail.account`,
following the `payment.provider` pattern. Building it, the provider record turned out to be bloat.
Dropping it.

**Odoo's own `google_gmail` sets the precedent, and it is the opposite of `payment.provider`:**

```python
google_gmail_client_id     = ICP.get_param('google_gmail_client_id')  # config param — ONE set
google_gmail_refresh_token = fields.Char(...)                         # token on the RECORD
```

Credentials in config params, tokens on records. That is exactly right here, because the two facts
have different cardinality:

| Fact | Cardinality | Home |
|---|---|---|
| Provider credentials | **One set per provider code.** Nobody has two Azure apps. | `ir.config_parameter`, namespaced per provider — as today |
| Which provider serves a mailbox | One of a fixed list | `Selection` on the mailbox |
| Credentials per address | **Many.** Per user, per provider, plus service accounts with no user at all. | `pan.mail.account` — genuinely needs to be a model |

A provider *record* would buy multiple credential sets per provider code — a feature nobody asked for
— at the cost of a model, a data migration, and rewriting every `config_parameter=` field on the
settings page. CLAUDE.md: *"Push back on feature requests that cause unnecessary bloat."* That
applies to my own plan.

It also means **no credential migration at all**: the Microsoft impl keeps reading its existing
`x_pan_outlook_pro.*` keys, and a future Google impl reads Odoo's own `google_gmail_*` keys. The
config params were never the problem.

## Why `pan.mail.account` survives the same scrutiny

Because its cardinality really is many-per-thing, and two of those cases have nowhere to live today:

| Fact | Today | Problem |
|---|---|---|
| User's tokens | `res.users.x_microsoft_access_token_encrypted` etc. | One token per user. Connect both Microsoft and Google and the second has nowhere to go. |
| Service mailbox tokens | *nowhere* | A Gmail shared mailbox is a real Workspace account with its own token and no Odoo user to hang it on. |

`pan.mail.account` solves both: **credentials for one email address on one provider**, with `user_id`
nullable. Set → a user's own connection. Null → a service account.

## The encryption trap

Tokens are Fernet-encrypted via `encryption_utils`, keyed on the database.

**Copy the ciphertext. Do not decrypt and re-encrypt.** Same key, same DB — moving the encrypted
string is lossless and cannot fail halfway. A decrypt/re-encrypt cycle can silently produce garbage
if the key is rotated or missing, and you would not find out until the next send.

This also means the migration is pure SQL and needs no ORM, which matters: `_compute_decrypted_tokens`
raises on a bad ciphertext, so an ORM-based migration would explode mid-flight and leave half the
users migrated.

## The stored-compute trap

`res.users.x_microsoft_oauth_connected` is **stored**, computed from
`x_microsoft_refresh_token_encrypted`, and used in view domains:

```python
# microsoft_mailbox.py
x_owner_user_id = fields.Many2one('res.users', domain="[('x_microsoft_oauth_connected', '=', True)]")
x_incoming_user_id = fields.Many2one('res.users', domain="[('x_microsoft_oauth_connected', '=', True)]")
```

Repointing it at `pan.mail.account` changes its `@api.depends`, which forces a recompute across every
user. Get the depends wrong and the field silently goes False — mailbox owner dropdowns empty out and
`_get_mailbox_and_user` starts falling back to the notification mailbox. Loud in production, invisible
in tests that create their own fixtures.

---

## Steps

Each step is one commit, tests green at every step.

### 1. `x_provider` on the mailbox — real dispatch

```python
# microsoft_mailbox.py
x_provider = fields.Selection([('microsoft', 'Microsoft 365')],
                              required=True, default='microsoft')

def _get_provider(self):
    return self.env['pan.mail.provider.%s' % self.x_provider]
```

Replaces the Phase 1 stub that hardcoded Microsoft. No credential migration — each provider impl
keeps owning its own config keys.

`default='microsoft'` is correct *only* while Microsoft is the only option: it makes existing
mailboxes migrate themselves with no script. Revisit when a second provider lands — at that point a
default becomes a footgun.

**Done when:** tests green; existing mailboxes get `x_provider = 'microsoft'`; graceful degradation
still holds (`mail.mail.send()` falls through to `super()` when no mailboxes exist at all).

**Status: done.**

### 2. `pan.mail.account` — credentials per address

```python
class PanMailAccount(models.Model):
    _name = 'pan.mail.account'
    provider (selection, required), email (required), user_id (nullable), active
    access_token_encrypted, refresh_token_encrypted, token_expiry, oauth_state
    access_token, refresh_token (compute/inverse, never stored)
    _unique_user_provider = UNIQUE(user_id, provider)   # NULL user_id repeats freely in postgres,
                                                        # which is what service accounts need
```

Model only. Nothing writes to it yet.

**Status: done.**

### 3. Migrate user tokens

`migrations/19.0.1.2.0/post-migrate.py`, pure SQL:

```sql
INSERT INTO pan_mail_account (provider_id, user_id, email, access_token_encrypted, ...)
SELECT :provider_id, u.id, p.email, u.x_microsoft_access_token_encrypted, ...
FROM res_users u JOIN res_partner p ON p.id = u.partner_id
WHERE u.x_microsoft_refresh_token_encrypted IS NOT NULL;
```

**Copy, do not move.** Leave the `res.users` columns in place this release — they are the rollback.
Drop them in a later one, once production has run on accounts for a while.

Verification query to run against a restored backup *before* trusting it:
```sql
SELECT (SELECT count(*) FROM res_users WHERE x_microsoft_refresh_token_encrypted IS NOT NULL)
     = (SELECT count(*) FROM pan_mail_account WHERE refresh_token_encrypted IS NOT NULL) AS ok;
```

**Status: done.** The script runs that query itself and logs an error on a mismatch, rather than
leaving it for someone to remember. Version is `19.0.1.2.0`, not the `19.0.2.0.0` sketched above —
this is a step in a refactor, not a new major.

`tests/test_account_migration.py` loads the script by path and runs it against real rows. Note the
fixture has to write the res_users columns in **raw SQL**: after step 4 the ORM fields are proxies,
so creating a user through the ORM would create the account too and the migration would correctly do
nothing. That test would have been green and worthless.

### 4. `res.users.x_microsoft_*` become proxies

Keep the fields, repoint them at the account so nothing else in the codebase changes yet:
`x_microsoft_oauth_connected` computes from `account_ids` filtered on the Microsoft provider.

Mind the stored-compute trap above. `@api.depends('x_pan_mail_account_ids.refresh_token_encrypted')`.

**Status: done.** Four things worth knowing, all of them decided while building it:

- **The token fields became unstored, not just repointed.** `x_microsoft_access_token_encrypted`,
  `..._refresh_token_encrypted` and `x_microsoft_token_expiry` are now compute/inverse pairs over the
  account. Odoo leaves the old columns in the table, which is what keeps the rollback intact — and
  what lets the migration test reproduce a pre-migration database.
- **The inverse creates the account on demand.** That is what lets `controllers/main.py` and
  `refresh_access_token()` keep writing to `res.users` unchanged. Clearing tokens for a user who has
  no account creates nothing: a blank account reads as a connection that was never made.
- **`x_microsoft_oauth_state` was deliberately *not* proxied.** It is written when the OAuth flow
  starts, before any account exists, so proxying it would create an empty account every time someone
  opened the connect page and walked away. It is a property of the browser round trip.
- **The migration realigns the stored flag.** Odoo does not recompute a stored field just because its
  `@api.depends` changed, so `x_microsoft_oauth_connected` would keep its old value — right for every
  migrated user, wrong for any the copy missed. One `UPDATE` in the same script makes the field and
  the accounts agree in both directions.

### 5. `_get_sending_account` returns an account, not a user

The interface already names it "account"; today it returns `res.users` because that is where tokens
live. This is the step that makes the name true.

Touches `graph_client.get_valid_token(user)` / `refresh_access_token(user)` and every `fetch_*(user=...)`
call. Mechanical but wide — worth its own commit, and the point where `tests/common.py`'s
`fake_get_valid_token` needs revisiting.

**Status: done.** The Graph client no longer knows `res.users` exists — every entry point that needed
a token now takes a `pan.mail.account`. Two things the plan did not anticipate:

- **The interface needed a second method, `_account_for_user(user)`.** `_get_sending_account` answers
  "whose credentials send this mail", which is a per-mailbox question that Gmail will answer with a
  service account and no user at all. Mailbox routing also asks a different question — "which account
  holds *this person's* credentials" — when the author's default mailbox has already decided the
  person. Collapsing the two would have silently changed who sends from a personal mailbox owned by
  somebody other than the author.
- **`mail.mail._get_mailbox_and_user` was renamed to `_get_mailbox_and_account`**, along with
  `_get_notification_mailbox_and_account` and `_get_missing_account_error`. Leaving the old names on
  methods that now return accounts is how the next reader gets misled.

`test_mailbox_routing`'s 15 rows keep testing the same decision table, asserting on `account.user_id`
rather than the account itself — the dimension the table exists to pin down is still *whose* token
sends, and dropping to "an account came back" would have quietly weakened all 15.

**What is deliberately left for Phase 4:** `controllers/main.py` and the OAuth wizard still write
tokens through the `res.users` proxies rather than to an account directly. That is fine while
Microsoft is the only provider — the proxy creates the account — and it is the natural place to clean
up when the fields are renamed.

---

## Verification

Unit tests cover none of the migration. Before calling Phase 2 done:

1. Restore a **production backup**, run the migration, run the verification query in step 3
2. Confirm every previously-connected user still shows `x_microsoft_oauth_connected = True`
3. Send one mail from a personal mailbox and one from a shared mailbox
4. Let the incoming cron run once
5. Confirm the mailbox owner dropdown still lists users (the stored-compute trap)

Rehearse on a restored backup twice. Not a dev DB — dev DBs do not have the messy real token states
that break migrations.
