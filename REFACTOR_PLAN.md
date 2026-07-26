# Refactor Plan: pan_mail_pro → pan_email_pro

Multi-provider email integration (Microsoft 365, Google Workspace, IMAP/SMTP).

Status: **proposal** — not yet approved. Sequencing: refactor first, rename last.

> **Revision note.** This plan was rewritten after reading the Odoo 19 source. Three load-bearing
> assumptions turned out to be wrong; §2 records them, because they were wrong in the direction of
> *more work than necessary* and the same instincts will resurface.

---

## 1. Summary

| # | Ask | Verdict |
|---|-----|---------|
| 2 | Split operations from provider implementations | **Right call.** But the split is smaller than it looks — Odoo 19 already owns half of it. |
| 1 | Rename to `pan_email_pro` | Mechanical. Last commit. |
| 3 | Zitadel for auth | **No.** Verified against source (§6). And the problem it was meant to solve is already solved — by Odoo. |

The current code is in decent shape. `mail_mail.py` reads as Microsoft-heavy (97 mentions) but nearly
all of it is field names, log tags, and error strings — the actual Graph call is one line. Mailbox
routing, partner matching, and threading are already provider-neutral. Genuinely provider-specific:

- `models/microsoft_graph_client.py` — all 886 lines
- `models/microsoft_incoming_mail.py:115-320` — raw Graph JSON keys leaking into the processor
- `models/res_users.py` + `models/res_config_settings.py` — single-provider token/credential storage

The 66 tests mock at the `requests` level and assert on the Graph JSON payload. That is the safety net
for the entire refactor: if the wire format stays identical, the tests prove the move was clean. Do
not weaken them into unit tests of the new abstraction.

---

## 2. Three assumptions that were wrong

Recorded deliberately — each one, believed, costs weeks of unnecessary work.

### ❌ "Odoo can't send from a user's own mailbox, that's why this module exists"

**Odoo 19 core has personal mail servers.** Not an addon — `addons/mail/models/ir_mail_server.py`:

```python
owner_user_id = fields.Many2one('res.users', 'Owner')
_unique_owner_user_id = models.Constraint("UNIQUE(owner_user_id)", "owner_user_id must be unique")
```

Plus `res.users.outgoing_mail_server_id`, self-service `action_setup_outgoing_mail_server()`,
per-server throttling (30/min default), autovacuum of orphaned servers, `from_filter` enforcement.
`microsoft_outlook` and `google_gmail` are mixins over both `ir.mail_server` **and** `fetchmail.server`.

**The manifest's justification is out of date for Odoo 19.** *"Microsoft Outlook App: No control over
sender - always notifications@..."* was true once. It isn't now. Rewrite the pitch (§7).

### ❌ "Customers must create their own app registration"

**Odoo runs its own OAuth relay, free with Enterprise:**

```python
_DEFAULT_GMAIL_IAP_ENDPOINT   = 'https://gmail.api.odoo.com'
_DEFAULT_OUTLOOK_IAP_ENDPOINT = 'https://outlook.api.odoo.com'

if not is_configured:  # use IAP
    if release.version_info[-1] != 'e':
        raise UserError(_('Please configure your Gmail credentials.'))
```

No client_id configured → Odoo brokers OAuth through **its own vendor-owned app**. Enterprise-gated
(not SaaS-only: Odoo.sh and on-prem Enterprise both qualify; Community does not). We target
Enterprise. The multi-tenant app + Publisher Verification programme I originally recommended building
— Odoo already built it, and your customers already have it.

**This dissolves the CASA question.** It's Odoo's OAuth client, so it's Odoo's assessment. See §6 for
the one catch.

### ❌ "SMTP/IMAP is a dead end; Graph is the future-proof choice"

Wrong, and this was the load-bearing argument for staying on Graph:

- **SMTP AUTH is not being retired** — only *Basic auth* is. OAuth SMTP AUTH continues. Timeline
  slipped: Basic auth works until 2026-12-31, off by default after, final removal announced H2 2027.
- **IMAP/POP are not retired.** `outlook.office.com/IMAP.AccessAsUser.All` is current and documented.
  TLS 1.0/1.1 gets blocked July 2026 — a TLS bump, not a protocol retirement.
- **What's actually dying is EWS** (phased Oct 2026 → Apr 2027, Graph mandated). Neither Odoo nor we
  use EWS. The "Microsoft is killing legacy protocols" noise in the ecosystem is EWS spillover.
- **Gmail is steering users *toward* IMAP** (Gmailify and POP fetch retired Jan 2026).

**And the decisive one — shared-mailbox SendAs works over SMTP.** Microsoft documents it:

> "In case of shared mailbox access using OAuth, an application needs to obtain the access token on
> behalf of a user but replace the **userName** field in the SASL XOAUTH2 encoded string with the
> email address of the shared mailbox."

Delegated token for user A + `user=shared@contoso.com` in the XOAUTH2 blob → sends as the shared
mailbox, honouring in-tenant SendAs. **`Mail.Send.Shared` is not a Graph exclusive.** Odoo's mixin
already exposes exactly this: `_generate_oauth2_string(self, user, refresh_token)`.

---

## 3. What is actually still ours

After the above, honestly:

| Capability | Odoo 19 native | pan_email_pro |
|---|---|---|
| Send from user's own mailbox | ✅ | — |
| Gmail/Outlook OAuth, no app registration | ✅ (IAP, Enterprise) | — |
| Incoming via IMAP → mail gateway (alias routing) | ✅ | — |
| **Multiple mailboxes per user + Send From dropdown** | ❌ `UNIQUE(owner_user_id)` | ✅ |
| **Shared / Notification mailbox as first-class objects** | ❌ no concept | ✅ |
| **Sync to partner chatter** (not just alias→model) | ❌ | ✅ |
| **2-way sync incl. Sent Items** | ❌ | ✅ |
| **Per-mailbox routing rules, block lists** | ❌ | ✅ |
| **`conversationId` threading** | ❌ | ✅ (Graph only) |

**The moat is the orchestration layer, not the transport.** The mailbox model, the routing, the
composer, the chatter sync. That is a liberating conclusion: it means the transport can be whatever
is cheapest per provider, and it is exactly what CLAUDE.md's "reuse Odoo native features" asks for.

---

## 4. Target architecture

### Transport per provider — deliberately asymmetric

| Provider | Transport | Auth | Rationale |
|---|---|---|---|
| **Microsoft** | **Graph REST (keep)** | our own app registration | 886 lines of working, tested code. Incoming sync via REST cursor is far simpler than an IMAP state machine. `conversationId` has no IMAP equivalent. No user-visible benefit to a rewrite. |
| **Google** | **SMTP + IMAP (XOAUTH2)** | reuse `google.gmail.mixin` → IAP | No Gmail REST adapter: no MIME building, no resumable uploads, no history API, no strict threading rules. **And no Google Cloud project and no CASA** (§6). |
| **Soverin / generic** | **SMTP + IMAP** | password on `pan.mail.account` | Same adapter as Google. Nearly free once Google lands. |

This is the abstraction paying for itself immediately: **one IMAP/SMTP adapter serves both Google and
Soverin.** My earlier estimate of a 3–4 week Gmail REST adapter was work we do not need to do.

### Reusing Odoo's Gmail mixin

Verified: the controller is generic over *any* model inheriting the mixin, not hardcoded to
`ir.mail_server`:

```python
if not isinstance(model, request.env.registry['google.gmail.mixin']):
    raise Forbidden()
```

So `pan.mail.account` can `_inherit = ['pan.mail.account', 'google.gmail.mixin']` and inherit, free:
the authorization URL, the IAP fallback, token refresh, CSRF handling, and
`_generate_oauth2_string(user, refresh_token)` — whose `user` parameter is the shared-mailbox trick.

**Do not override `_SERVICE_SCOPE`.** IAP works because Odoo's OAuth app is consented for *Odoo's*
scopes. Change them and IAP breaks.

### Models

Two new, following the `payment.provider` pattern (a `code` selection with guarded overrides) — the
canonical Odoo approach per CLAUDE.md.

**`pan.mail.provider`** — one record per configured provider. Replaces the `x_microsoft_client_id`/
`_secret`/`_tenant_id` config params.
```
name, code (microsoft|google|imap), state
client_id, client_secret_encrypted, tenant_id, scopes   ← empty ⇒ use Odoo IAP (google) / vendor app
```

**`pan.mail.account`** — **credentials for one email address on one provider.** The keystone; it
unifies three cases that look different but aren't:
```
provider_id, email, state
user_id (nullable)   ← set = a user's own connection; null = a service account
access_token_encrypted, refresh_token_encrypted, token_expiry   (Graph)
google_gmail_refresh_token …                                    (inherited from the mixin)
password_encrypted                                              (IMAP)
```
`user_id = null` is what makes a Gmail "shared" mailbox work: `sales@company.com` is a real Workspace
user — authorize it once, store the token. No delegation, no service account, no domain-wide key.

**`pan.mailbox`** — today's `x_microsoft.mailbox` plus `provider_id` and `x_account_id`. Sync/routing/
type fields carry over unchanged.

### Keep the type vocabulary; move the mechanics

Do **not** replace Personal/Shared/Notification with orthogonal `token_source`/`visibility` fields.
Correct in the abstract, wrong in practice: it churns the UI, views, docs and every test to expose a
distinction users don't care about. Instead:

```python
provider._get_sending_account(mailbox, mail)   # Microsoft: sender's account (Mail.Send.Shared)
                                               # Google:    the mailbox's own account
provider._supported_mailbox_types()
```

"Shared" means *sender's own token* on Microsoft and *the mailbox's own token* on Google. One genuine
semantic loss on Google: no per-user attribution — one shared token, so the sent item doesn't land in
the sender's Sent folder. Acceptable; document it.

### Operations layer

| Operation | Lives in | Calls |
|---|---|---|
| Send routing, mailbox resolution, notification fallback | `mail_mail.py` (logic unchanged) | `provider._send(mail, mailbox, account)` |
| Sync loop, cursor, dedup, partner matching, threading, alias routing | `pan_mail_fetcher.py` (from `microsoft_incoming_mail.py:446+`) | `provider._fetch_messages(...)` |
| OAuth dance | `controllers/main.py` (Graph) / Odoo's (Gmail) | `provider._get_auth_url()`, `._exchange_code()`, `._refresh()` |

Adapters implement only: `_send`, `_fetch_messages`, `_get_message`, `_supported_mailbox_types`,
`_get_sending_account`, and the auth quartet.

Layout: one module, providers as sub-packages (`providers/microsoft/`, `providers/imap/`). Not
separate addons — that means three App Store listings for one product.

### The normalized message dict

The processor reads Graph JSON directly in ~20 places. Every provider normalizes into one shape;
everything below `microsoft_incoming_mail.py:446` then works unchanged:

```python
{
  'message_id':          str,   # RFC5322 Message-ID   (Graph internetMessageId | IMAP header)
  'provider_message_id': str,   # Graph id             | IMAP UID
  'thread_id':           str,   # Graph conversationId | derived from References
  'in_reply_to', 'references', 'subject', 'date',
  'from': (name, email), 'to': [...], 'cc': [...],
  'body_html',
  'attachments': [{'name', 'content_type', 'content', 'is_inline', 'cid'}],
}
```

For IMAP this is just `email.message_from_bytes()` — Python's stdlib parser. That is the whole
normalization layer for two of the three providers.

---

## 5. Phasing

New models get final names immediately; existing ones are renamed in Phase 5.

| Phase | Work | Est. | Ships |
|---|---|---|---|
| **1** | Provider interface + `providers/microsoft/`. Move the 886-line client behind it. Normalize the message dict; rewrite `_fetch_folder`/`_process_message`. **Tests stay green with zero changes to their Graph payload assertions.** | 2–3 wk | internal |
| **2** | `pan.mail.provider` + `pan.mail.account`. Migrate `res.users.x_microsoft_*` tokens + settings creds. Highest-risk migration: a botched token migration disconnects every user. | 1–2 wk | 19.0.2.0.0 |
| **3** | `providers/imap/` — SMTP+IMAP XOAUTH2, reusing `google.gmail.mixin`. Delivers **Google and Soverin together**. | 2–3 wk | 19.0.3.0.0 |
| **4** | Rename: module, models, fields, XML ids, `odoo.addons.pan_mail_pro` test patch paths. One mechanical commit + migration. | 3–5 d | **pan_email_pro 19.0.4.0.0** |

Phase 1 is the one to protect. Land it clean and 3 is additive. Land it muddy and every provider
inherits the mud.

**The IMAP state machine is the real risk in Phase 3** — UIDVALIDITY, UIDNEXT, folder handling.
Genuinely harder than Graph's `receivedDateTime` cursor. It is the reason Phase 3 isn't 1 week.

### Rename mechanics (Phase 4)

You accepted breakage. Fine — but the *data* must survive: losing `x_microsoft_message_id` on
`mail.message` breaks reply threading for all historical mail.

Odoo cannot rename a module from inside its own migration script (chicken-and-egg: migrations only
run for an already-installed module under that name).

1. Pre-upgrade SQL: `UPDATE ir_module_module SET name='pan_email_pro' WHERE name='pan_mail_pro'`
   and the same on `ir_model_data.module`
2. Then upgrade `pan_email_pro`; its migrations rename models/tables/columns
3. Rehearse against a **restored production backup**, not a dev DB. Twice.

**Commercial flag:** if this is App Store listed, a technical-name change is a *new listing* — reviews,
ratings, and the upgrade path don't transfer. Decide deliberately.

---

## 6. Authentication

### Zitadel: no

Verified against Zitadel `main` @ `8395d43`. Three findings, each fatal alone:

- **Upstream tokens live ~1 hour** (`MaxIdPIntentLifetime: 1h`, a `systemdefaults` value — not tunable
  on Zitadel Cloud).
- **The intent is single-use** — using it to create a session consumes it. The login path destroys the
  very thing holding the token.
- **Nothing refreshes anything.** Tokens live only on the transient intent aggregate; the permanent
  `idp_user_link` stores none. Zitadel stores the refresh token and never uses it.

[Issue #7851](https://github.com/zitadel/zitadel/issues/7851) requests exactly this and is open with no
commitment. CVE-2025-46815 was that intents *didn't* expire — the fix *introduced* the cap. They're
moving away from long-lived upstream tokens.

And it wouldn't help: Zitadel's own Entra guide still requires an app registration per customer. It
**relocates** the client_id/secret and adds a component to operate.

Zitadel answers "who is logging into Odoo." Not "give me Rutger's token at 3am."

### Google: use Odoo's IAP. One catch.

Because the Google transport is SMTP/IMAP (§4), we inherit `google.gmail.mixin` and get the IAP
fallback — **no Google Cloud project, no verification, no CASA**. It's Odoo's OAuth client.

**⚠️ The catch, and it needs an answer before Phase 3 ships:** the IAP relay is Odoo's app and Odoo's
infrastructure, keyed on `db_uuid` against the Enterprise subscription. The mixin is public and the
controller accepts any model that inherits it — so this is *technically* sanctioned by the code. Is it
*commercially* sanctioned for a third-party addon to route its OAuth through Odoo's relay? Unknown.
**Ask Odoo before shipping.** If they say no, fall back to a customer-owned OAuth client — which gets
the Internal-Use-App exemption anyway (no CASA), at the cost of a setup guide.

For reference, if we ever own the Google client ourselves: `gmail.send` is *sensitive* (form + review),
but `https://mail.google.com/` — required for SMTP+IMAP — is **restricted**, so CASA Tier 2,
~€500–4,500/yr, forever.

### Domain-wide delegation: rejected

DWD (a service account impersonating any mailbox) looks like the "just give access" answer. It is a
trap:

- **It cannot be scoped to a subset of users.** Google, verbatim: *"OAuth scopes don't restrict which
  users the service account can impersonate"*; *"the app has access to the data belonging to **all of
  your users**"*. No OU or group field exists. Scope limits *what*, never *whom*. A JSON key in the
  Odoo database is a domain-takeover primitive — see [Unit 42](https://unit42.paloaltonetworks.com/critical-risk-in-google-workspace-delegation-feature/)
  and [DeleFriend](https://www.hunters.security/en/blog/delefriend-a-newly-discovered-design-flaw-in-domain-wide-delegation-could-leave-google-workspace-vulnerable-for-takeover).
- **Service account keys are off by default** in orgs created on/after 2024-05-03
  (`iam.disableServiceAccountKeyCreation`).
- **Google discourages it**: *"Avoid using domain-wide delegation if you can accomplish your task
  directly."*

And it buys nothing we don't already get from the mixin + IAP.

### Gmail's own delegation: doesn't work via API

For the record, since it's the obvious thing to try. Gmail's "Grant access to your account" (max 25
delegates) is **not** exposed to the Gmail API: [`userId`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list)
accepts only the authenticated user, [`settings.delegates`](https://developers.google.com/workspace/gmail/api/guides/delegate_settings)
only *manages* the relationship, and there's an open request for exactly this
([#319822815](https://issuetracker.google.com/issues/319822815)). Moot now that the transport is IMAP,
but it's why `user_id = null` accounts exist.

### Microsoft: keep our own app registration

Graph needs `Mail.Send.Shared`, which Odoo's IAP app doesn't hold. Consider a vendor-owned
multi-tenant app to replace per-customer registration (admin clicks one consent link instead of ~15
portal steps). **Publisher Verification is a prerequisite** — free, but needs a verified Cloud Partner
Program account and a real publisher domain; since Nov 2020 users cannot consent to unverified
multi-tenant apps. Redirect URIs cap at 256, HTTPS, no useful wildcard.

Lower priority than it was: Odoo's IAP already covers the *personal Outlook mailbox* case for
Enterprise customers, so this only buys simpler onboarding for the shared-mailbox tier.

---

## 7. The uncomfortable strategic question

Odoo 19 Enterprise natively does: per-user OAuth mail servers, Gmail + Outlook, no app registration,
outgoing + incoming. Three of the four bullet points in the manifest's "Why this module?" are now
answered by the platform.

What remains is real and defensible (§3) — multi-mailbox, shared mailboxes, Send From, chatter sync,
2-way sync, routing rules. But it is a **narrower and different pitch** than "email integration for
Microsoft 365", and the module description still sells the old one.

Worth deciding before writing code: is pan_email_pro *"Microsoft 365 email for Odoo"* (increasingly
commoditized) or *"multi-mailbox, shared-mailbox and chatter-sync email that Odoo can't do"*
(defensible, provider-agnostic, and the natural home for Gmail + Soverin)? The second framing is what
this refactor is actually building. The manifest should say so.

---

## 8. Open decisions

1. **Odoo IAP** — ask Odoo whether a third-party addon may use their relay. Gates the Phase 3 auth
   design (fallback: customer-owned client, no CASA either way).
2. **App Store listing** — accept a new listing, or keep the technical name and rename only the label?
3. **Positioning** — §7. Affects the manifest, docs, and pricing more than the code.
4. **Microsoft multi-tenant app** — worth Publisher Verification, now that IAP covers personal
   mailboxes and only the shared tier benefits?

## 9. To verify before committing

- **Throughput.** SMTP client submission is documented at **30 msg/min**; Graph's limits are worded
  differently (10k req/10min, max 4 concurrent per mailbox). Whether Graph genuinely escapes a ~30/min
  Exchange ceiling is **contested and undocumented**. If throughput matters, measure it — don't reason
  from docs.
- **Gmail alias over SMTP.** Verified aliases work, but Gmail may render `From: you@gmail.com on
  behalf of other@domain.com` unless the alias has its own SMTP credentials. Under-documented for the
  XOAUTH2 + alias combination specifically. Test before promising Gmail shared mailboxes.
