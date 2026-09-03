# Architecture

Technical documentation for developers working on the Mail Pro module.

This file is the single source of truth for **design**: what the models are,
why the seams sit where they sit, and which decisions are load-bearing.
[CLAUDE.md](CLAUDE.md) covers the *workflow* — commands, environments, CI, and
the gotchas of working in Odoo — and deliberately does not repeat any of this.

---

## 1. Overview

### Purpose

Complete Microsoft 365, Google Workspace and IMAP/SMTP email integration for
Odoo — send and receive via the Graph API, the Gmail API or the mail protocols
themselves, with proper threading, partner matching and visibility into where
mail actually landed.

### Vocabulary

Two worlds and one bridge. Every word below has exactly one meaning, and the
code, the views and the documentation use the same one.

| Term | Meaning | In code |
|------|---------|---------|
| **Chatter** | Odoo's side: a `mail.message` on a record | `mail.message`, `message_post()`, `message_new()` |
| **Email** | The provider's side: a message in a mailbox folder | the normalized message in `mail.provider.client` |
| **Mailbox** | An address Mail Pro sends from and, when its sync mode says so, reads | `pan.mail.mailbox` |
| **Account** | Credentials for one address on one provider | `pan.mail.account` |
| **Provider** | Where the mail lives: `outlook`, `gmail` or `imap` | `mail.provider.client`, `PROVIDER_SELECTION` |
| **Outgoing** | Chatter → email. Odoo composes, the provider sends | `mail.mail` (`_resolve_route`, `send_message`) |
| **Incoming** | Email → chatter. The provider is read, the matcher decides, Odoo posts | `pan.mail.fetcher`, `pan.mail.matcher` |
| **Sync** | The user's word for the incoming flow and its settings | `sync_mode`, `last_sync_date`, "Sync Now" |
| **Send From** | The mailbox a mail leaves through | `x_send_from_mailbox_id`, `x_default_mailbox_id` |
| **Direction** | Which way an *email* went for its mailbox, whichever flow carried it | `mail.message.x_direction` |

Direction and flow are different axes, and the Sent folder is where they part.
Mail a user sent from Outlook itself reaches Odoo through the *incoming* flow
(it is fetched), but its direction is `outgoing` (the mailbox sent it). The
lens asks about direction; code paths are named after flow. Four quadrants:

| | Email side (the mailbox) | Chatter side (the record) |
|---|---|---|
| **Outgoing** | Sent by the mailbox — from Odoo (send flow) or from a mail client (Sent folder, fetch flow) | A message composed in Odoo that goes out as email |
| **Incoming** | Received by the mailbox (Inbox, fetch flow) | A message Mail Pro posts on a record from an email |

Naming rules that follow:

- A model of ours is `pan.mail.<thing>` with plain field names. `x_` is
  reserved for fields added to Odoo's own models (`mail.message.x_direction`,
  `res.users.x_default_mailbox_id`), which is what Odoo.sh asks for.
- Nothing outside `models/providers/<vendor>/` carries a vendor's name.
  `microsoft.graph.client` is Microsoft's; `pan.mail.mailbox` is nobody's.
- Configuration parameters live under `pan_mail_pro.`; a vendor-specific one
  carries the vendor in the key (`pan_mail_pro.microsoft_client_id`).
- Log tags name the flow or the vendor, never both: `[Outgoing Mail]`,
  `[Incoming Mail]`, `[Mail Matcher]`, `[OAuth]`, `[Graph API]`,
  `[Gmail API]`, `[IMAP]`, `[SMTP]`, `[Mail AI]`, `[Encryption]`, and
  `[Mail Pro]` for setup and housekeeping.

The names read `microsoft.*` and `x_microsoft_*` until 19.0.6.0.0, from the
time the module was an Outlook-only add-on. The rename was deliberately one
mechanical release rather than a series of partial ones, so every stored id
migrated once; `migrations/19.0.6.0.0/` is the record of what moved where.

### Provider abstraction

Everything wire-specific — how a mail is sent, how remote messages are listed
and read, which credentials to use — lives behind one contract,
`mail.provider.client`. Everything else — mailbox routing, partner matching,
threading, chatter posting — is provider-neutral and never touches a Graph or
Gmail JSON key. A mailbox names its provider with `provider` and dispatches
via `mailbox._get_client()`, which resolves the code through the registry in
`models/mail_provider_client.py`.

That neutrality is what made the third provider cheap. IMAP/SMTP has no OAuth,
no server-side message id and no thread id, and none of that reaches a caller:
credentials answer `account_is_connected()` instead of "has a refresh token", a
message id is the folder-scoped `folder:uidvalidity:uid` triple, and the thread
key is the root of the References chain.

There is exactly one layer: the client *is* the contract implementation, and it
lives in `models/providers/<vendor>/`. Credentials are a `pan.mail.account`, and
the client is what decides which account applies — `resolve_sending_account()`
and `resolve_receiving_account()` — because that is where providers genuinely
diverge.

### Capability differences

Providers disagree about sending as somebody else, which is why
`resolve_sending_account()` is the client's job and not the caller's:

| | Microsoft 365 (`outlook`) | Gmail (`gmail`) | IMAP/SMTP (`imap`) |
|---|---|---|---|
| Auth | OAuth 2.0 | OAuth 2.0 | server + login + password |
| Shared mailbox | Yes (SendAs + author's own token) | Its own Workspace account (`user_id` null) | Its own login (`user_id` null) |
| Delegation | — | Delegated account / Google Group | — |
| Folders | `Inbox` / `SentItems` | `INBOX` / `SENT` labels | `INBOX` / `\Sent` special-use |
| Thread key | `conversationId` | `threadId` | root of the `References` chain |
| Message id | Graph id | Gmail id | `folder:uidvalidity:uid` |
| Send flow | draft → send | RFC822 MIME | SMTP + IMAP APPEND to Sent |
| Message-ID | returned by the API | set by us on the MIME | set by us on the MIME |

### Model map

**The contract and its implementations**

| Model | Purpose |
|-------|---------|
| `mail.provider.client` | The contract (abstract): resolve credentials, send, fetch, normalize |
| `microsoft.graph.client` | Microsoft 365 implementation — all Graph API calls |
| `google.gmail.client` | Google Workspace implementation — all Gmail API calls |
| `imap.smtp.client` | IMAP/SMTP implementation — `imaplib` + `smtplib`, no OAuth |

**Configuration and credentials**

| Model | Purpose |
|-------|---------|
| `pan.mail.mailbox` | Mailbox configuration (email, type, sync mode, routing, `provider`) |
| `pan.mail.account` | Credentials for one address on one provider (nullable `user_id`) |
| `pan.mail.domain` | One row per internal domain; the one definition of "is this address ours?" |
| `pan.mail.setup` | The five setup steps and the phase they add up to (abstract) |
| `res.config.settings` | Module settings (provider choice, client id, secret, tenant) |
| `res.users` | Default mailbox + OAuth state; **no** token fields since 19.0.5.0.0 |
| `res.partner` | Contact block list field (`x_email_sync_blocked`) |

**Moving mail**

| Model | Purpose |
|-------|---------|
| `pan.mail.fetcher` | Incoming flow (cron): fetch, filter, match, post. Provider-neutral |
| `mail.mail` | Outgoing override — resolves a route, then hands to the provider |
| `mail.compose.message` | Composer "Send From" dropdown + setup warning |
| `mail.message` | The communication lens fields, plus one legacy thread column |
| `mail.alias` | Small extension so an alias can name a mailbox |

**Deciding where mail belongs**

| Model | Purpose |
|-------|---------|
| `pan.mail.matcher` | The thread-matching rule ladder. No provider, no HTTP, no mailbox needed |
| `pan.mail.message.ref` | Every Message-ID under which one `mail.message` may be referenced |
| `pan.mail.thread.link` | Provider thread handle → Odoo record, **scoped to the mailbox** |

**Seeing what happened**

| Model | Purpose |
|-------|---------|
| `pan.mail.routing.log` | One row per delivered mail: rule, confidence, rejected candidates |
| `pan.mail.item` | Triage queue for mail that reached Odoo but landed nowhere |
| `pan.mail.coverage` | Transient report: how much mail actually lands on a document |

**AI (opt-in, off by default)**

| Model | Purpose |
|-------|---------|
| `pan.mail.ai` | The AI contract (abstract) + backend registry |
| `pan.mail.ai.null` | The default. A real backend that returns nothing |
| `pan.mail.ai.claude` | Claude backend — the only place an AI SDK may be imported |

`pan.mail.account` holds the credentials that used to live on `res.users`. An
account with a `user_id` is a person's own connection; an account with none is a
service account — how a Gmail shared mailbox works, where the address is a real
Workspace account with no Odoo user behind it.

### Module structure

```
pan_mail_pro/
├── models/
│   ├── mail_provider_client.py    # mail.provider.client — the contract + registry
│   ├── providers/                 # The only place provider payloads are understood
│   │   ├── microsoft/graph_client.py
│   │   ├── google/gmail_client.py
│   │   ├── imap_smtp/imap_client.py
│   │   └── mime_utils.py          # Outgoing MIME, shared by the two MIME senders
│   ├── ai/                        # The only place an AI SDK may be imported
│   │   ├── pan_mail_ai.py         # Contract + registry + null backend
│   │   └── claude/claude_backend.py
│   ├── pan_mail_mailbox.py        # Mailbox config + routing + provider dispatch
│   ├── pan_mail_account.py        # Per-address credentials
│   ├── pan_mail_domain.py         # Internal domains + the fail-closed gate
│   ├── pan_mail_setup.py          # Setup vs syncing: the five mandatory steps
│   ├── pan_mail_fetcher.py        # Incoming processor (provider-neutral)
│   ├── pan_mail_matcher.py        # Thread matching rule ladder
│   ├── pan_mail_thread_index.py   # pan.mail.message.ref + pan.mail.thread.link
│   ├── pan_mail_routing_log.py
│   ├── pan_mail_item.py           # Triage queue
│   ├── pan_mail_coverage.py       # Coverage report (TransientModel)
│   ├── mail_mail.py               # Outgoing override + route resolution
│   ├── mail_message.py            # Threading keys + communication lens
│   ├── mail_compose_message.py
│   ├── res_users.py / res_partner.py / res_config_settings.py
│   └── encryption_utils.py        # Fernet encryption
├── controllers/main.py            # OAuth callbacks (Microsoft + Google, one handler)
├── migrations/                    # 19.0.1.0.5, 2.1.0, 3.3.0, 4.0.0, 5.0.0, 6.0.0
├── views/  data/  security/  static/
├── tests/                         # 29 files; see §12
└── tools/                         # CI helpers
```

There is no `wizard/` directory. Connecting an account is a controller redirect,
not a wizard.

### Two phases: setup, then syncing

The module is either being **set up** or **syncing**. `pan.mail.setup` owns the
difference, and everything that carries mail asks it rather than checking a
condition of its own.

| # | Step | Answered by |
|---|------|-------------|
| 1 | Email provider | `pan_mail_pro.setup_provider` |
| 2 | Provider credentials | the app registration, or the IMAP accounts |
| 3 | Connected account | any connected `pan.mail.account` on that provider |
| 4 | Internal domains | at least one `pan.mail.domain` row |
| 5 | A notification mailbox | a mailbox with `is_notification_mailbox` ticked that can send |

All five are mandatory. There is no partial service: while the phase is `setup`
the incoming cron returns without fetching, "Sync Now" refuses with the step
that is missing, and internal notifications queue with a readable reason instead
of being cancelled. Nothing here has an opinion once the phase is `syncing`.

Three properties are worth naming, because each was a bug first:

- **The answers are about the database, not about the reader.** "Connected"
  means *some* account on the provider is connected. A second admin opening the
  settings page must not be told the product is unconfigured because they
  personally have not signed in. The Connect button keeps its own user-scoped
  question; the phase does not.
- **Order is the contract.** Step 5 creates a mailbox owned by whoever is
  setting up, so step 3 has to come first. Step 4 comes before any mailbox
  exists because a mailbox refuses to enable sync while the domains are
  unanswered, and meeting that as a validation error afterwards is worse than
  being asked in order.
- **Inviting the team is not on this page at all.** Mail flows with one
  connected account, so a colleague who has not signed in is a rollout task,
  not a gate — and the invite button already lives on the user list, where the
  users are. A second copy in Settings was a second front door to one action.

---

## 2. Mailbox types

| Type | Who sees it? | Whose credentials? | Use case |
|------|--------------|--------------------|----------|
| **Personal** | Only owner | Owner's | User's own mailbox (john@company.com) |
| **Shared** | Everyone | Sender's own on Microsoft 365; the address's own on Gmail and IMAP | Team mailbox (sales@company.com) |

**Notification** is not a type. Exactly one mailbox has `is_notification_mailbox`
ticked, and system email goes out from it with its owner's credentials. The tick
box is editable straight from the mailbox list, so moving it is untick here,
tick there — and the settings page has no form of its own for it, it reports
which mailbox carries it. It used
to be a third Type value, which forced "personal or shared?" to be answerable
with "neither" and made every rule about types carry an exception. As a tick box
it is a property of one mailbox, and the one exception left is explicit: the
notification mailbox is personal but sendable by any author, because that is the
job it exists to do.

Which credentials a mailbox runs on is asked of the provider
(`resolve_sending_account` / `resolve_receiving_account`), never assumed by the
caller: only Microsoft 365 lets one person send as another with their own token.

**Personal** — auto-created when a user connects (if the admin setting allows).
`owner_user_id` links it to its owner, and only the owner sees it in the
composer dropdown.

**Shared** — on Microsoft 365 each user sends with their **own** OAuth token, so
they need `Mail.ReadWrite.Shared` in Azure and SendAs rights in Exchange. On
Gmail and IMAP/SMTP the address is its own account with its own credentials and
no owner; nothing is borrowed from the sender.

**Notification** — for system mail (activity reminders, mentions). Uses the
owner's token, and only one may be active. **Required before any mailbox can
enable incoming sync**, because mail triggered by an external author has to go
out from somewhere.

---

## 3. Sync modes and filtering

> **Shipped, with two exceptions.** The counterpart rule and the gate ladder
> below are the current code; so is the boundary in §9.10. Still design rather
> than code: the BCC allow-list, the "no follower from CC" limit and the
> inbound half of the takeover (§10), tracked in
> [#37](https://github.com/pantalytics/pan_mail_pro/issues/37) and
> [#38](https://github.com/pantalytics/pan_mail_pro/issues/38).


### One control, not six

`sync_mode` is a single three-way choice, and every question the mailbox form
used to ask separately is an answer to it:

| `sync_mode` | Meaning |
|---------------|---------|
| `none` | Send only. Nothing is imported. |
| `known_partners` | Import mail from addresses that are already contacts. |
| `all` | Import mail from anyone. |

`x_incoming_sync`, `x_sync_unknown_contacts`, `x_sync_inbox`, `x_sync_sent` and
`x_incoming_enabled` were computes over exactly this choice — five things that
could disagree with the one field that decided. Code asks
`mailbox._syncs_incoming()`, which reads the mode through the `SYNCING_MODES`
allow-list, so an unset value means "do not import" rather than "import
everything".

Two booleans remain, and neither is a mode:

| Field | Type | Description |
|-------|------|-------------|
| `routing_smart` | Boolean | Interlock keeping AI auto-routing off. See §8 |
| `queue_unknown_contacts` | Boolean | Hold unknown senders in the triage queue |

### Internal domains are a gate, not a preference

`pan.mail.domain` is the only place that answers "is this address one of ours?",
and it is a table: one row per domain, cleaned on the way in so a pasted address
or a stray `@` lands as a bare domain. **No mailbox can exist while the table is
empty**, and a sync run aborts if it is emptied later.

The gate sits on the mailbox rather than on the sync switch, because a mailbox
is the moment Mail Pro takes over the company's mail: the SMTP takeover fires
there and sending starts there. Gating only the switch left the setting reading
as an option belonging to sync — which is exactly how it read right up until it
mattered. Not at install, though: an empty database has no domains to derive
and nobody to protect.

**A configured list is not a complete one**, and only the second is worth
anything. `uncovered_domains()` compares the list against the domains this
database can demonstrate belong to the company — its mailboxes and its internal
users' own addresses — and saving the settings with one of them missing is
refused. The users are the source that matters: a company that acquired another
has colleagues on its domain long before it has mailboxes on it, so a list
built from mailboxes alone reads as complete and treats those colleagues as
outsiders.

Two exclusions, both load-bearing. Portal users, since a customer with a login
is not the company. And public mail providers, since a colleague whose Odoo
login is a personal address would otherwise put `gmail.com` on the internal
list — and an internal domain stops mail being synced, so that one setting
silently stops logging every customer who uses the same provider. The dropped
case is a company that genuinely runs on a public domain: it gets no help from
the suggestion and types its domain in by hand, which is rare, loud, and
recoverable in one field. The reverse error is none of those.

This used to read `mail.alias.domain`, where "no domains configured" meant
"nothing is internal" — so a database that never set it up synced every
internal email into Odoo. Fail-closed, because the failure mode is a data leak.
Alias domains still feed `suggest_domains()`; they no longer decide anything.

**The filter has no off switch.** Not globally and not per mailbox — see
§9.12.

### The gate ladder

Whether a message may enter Odoo is decided by an ordered list of named gates,
the same shape the matcher uses to decide where it goes (§4). Order is the
contract: a gate may assume every gate before it passed, and may leave what it
resolved in the context for the ones after it.

| # | Gate | Refuses | Leaves a trace |
|---|------|---------|----------------|
| 1 | `_gate_duplicate` | Message-ID already in `mail.message` | no |
| 2 | `_gate_odoo_originated` | our own `X-Odoo-*` headers came back | no |
| 3 | `_gate_counterpart` | a sent item with no recipient | no |
| 4 | `_gate_internal_domain` | every party to the mail is ours | no |
| 5 | `_gate_blocked_contact` | `x_email_sync_blocked` | no, deliberately |
| 6 | `_gate_internal_user` | the address has an Odoo user | no |
| 7 | `_gate_sync_mode` | what the mailbox was told to accept | **yes** |

Gate 3 is where direction lives: the inbox reads the `From`, Sent Items reads
the `To`. It collects the candidates and gate 4 chooses among them, so the whole
internal decision sits in one place rather than being split by direction — which
is how gate 4 came to guard one folder and not the other.

Whether a refusal reaches `pan.mail.item` is declared by the gate, because it
is a property of the refusal. Gate 7 records: an unknown contact is a decision
a person may want to reverse, and the queue is where they reverse it. The
others refuse things nobody wants back — and gate 5 must leave no trace at all,
since a block list is an objection to processing and a queue row naming the
person would be processing.

Adding a rule means adding a method and a line to `_gate_rules()`. Before this
existed the seven decisions were bare `return False` statements strewn through
a two-hundred-line method, which is how gate 4 came to guard one folder and not
the other.

Gate 1 is broader than its name: "already in Odoo" is answered by
`pan.mail.matcher._resolve_message_id()`, which reads the ref index — including
the id the provider minted when *we* sent the mail — as well as
`mail.message.message_id`. So a mail Odoo sent, filed in Sent by the provider
and read back on the next run, is refused there rather than needing a rule of
its own.

### The counterpart rule

One question decides whether a mail is logged: **is the counterpart external?**

The counterpart is the other side of the correspondence, and which field holds
it depends on the folder.

| Folder | Counterpart | Logged when |
|--------|-------------|-------------|
| Inbox | the `From` | the sender is external |
| Sent Items | the `To` | any To address is external |

With several recipients the first external one becomes the counterpart and the
mail is logged on them. Only when every recipient is ours is it internal
traffic, and then nothing enters.

An earlier version checked the *sender* in both folders and, noticing that the
sender of a sent item is always us, concluded that Sent Items needed no
internal check at all. The observation was right and the conclusion was wrong:
the answer is to check the counterpart, not to stop checking. That gap is how a
company's own internal mail reached Odoo and was mailed back out. See §9.10.

Three consequences, each dropping a case on purpose.

**An internal user and an internal address are the same thing here.** A
colleague with an Odoo account and a bare `planning@company.com` with no user
are both simply not-external. The domain gate is the one that settles it, which
is what makes a shared or functional address behave like the colleague it
belongs to; asking `partner.user_ids` is precisely what let `planning@` through
for months. The `user_ids` gate stays as a narrower second net, for the
colleague whose Odoo login is on a domain the list does not carry.

**CC does not enter the decision.** A mail to `planning@` with a customer in Cc
is not logged, so a real customer mail goes missing. That is a completeness
loss; the reverse error, logging internal mail, is a confidentiality loss. A
rule this blunt errs somewhere, and it errs toward silence.

**A mail with no external counterpart leaves a log line, not a queue row.**
`pan.mail.item` is the queue of skips a person can reverse, and internal mail is
the one refusal that must never be reversible: an Import button there would be a
button for leaking. The refusal the ladder logs carries the mailbox, the
Message-ID, the reason and the time — enough to answer why a mail is missing
from Odoo, and as much as may be kept about a mail we declined to read.

### The four paths

Mail crosses between the mailbox and Odoo in four ways. The counterpart rule is
an *ingestion* control, so it governs two of them.

| | Sending | Receiving |
|---|---|---|
| **Mailbox** | user writes in Outlook; sync reads Sent Items. **Filter on the To.** | mail lands in the inbox; sync reads it. **Filter on the From.** |
| **Odoo** | chatter or notification; `mail.mail.send()` routes it out. **No filter.** | nothing. A Mail Pro database does not receive through `mail.alias`. |

**Sending from Odoo is never filtered.** A person clicked send, or a colleague
was legitimately notified about a task; suppressing internal mail here would
break ordinary Odoo behaviour. What this path needs instead is dedup, because
the provider files a copy in Sent Items and the next sync run reads it back.
That is what the Odoo-originated pre-filters above are for, which makes them
load-bearing rather than an optimisation.

**Receiving into Odoo is supposed to be empty.** Mail Pro replaces the inbound
gateway with mailboxes, so `mail.alias` is no longer an address that receives.
Keeping that path empty is an act rather than a fact — see §10.

`mail.alias` records are still in use, in a different job: `alias_id` on the
mailbox is read for `alias_model_id` and `alias_defaults`, so the fetcher knows
that mail for this mailbox becomes a `crm.lead`. The address is unused; the
record is not. Deleting "unused" aliases breaks routing.

### What CC and BCC do

**CC is stored and acted on by nothing.** Everyone on the mail already saw it,
so keeping it costs no confidentiality, and it is what a future reply-all would
read. It never creates a `res.partner` and never creates a follower. The first
would build a contact database out of other companies' colleagues; the second
is the mechanism behind §9.10.

**BCC must not cross the provider boundary.** A received message carries no BCC
list, so an inbox sync is safe by construction. The sender's own copy in Sent
Items does carry it, and Sent Items is exactly what this module reads. What
leaks there is the recipient list rather than the content, which is the whole
point of BCC.

So the normalized message has no `bcc` key, and `headers` is an allow-list
rather than a copy of the message's headers. It lives in the contract as
`HEADER_ALLOWLIST`, applied by `normalize_headers()`, which every client calls
on its way out of `_normalize_message`. An allow-list in the contract also
covers providers nobody has written yet; a strip call in each client does not.

The list carries only what something actually reads:

| Header | Read by |
|---|---|
| `in-reply-to`, `references` | `pan.mail.matcher`, to walk a reply back to its record |
| `x-odoo-model`, `x-odoo-record-id` | `pan.mail.matcher`, for our own mail coming back to us |
| `x-odoo-mail-id` | the fetcher's loop guard |

Everything else a message needs — sender, To, CC, subject, date, Message-ID —
is already a normalized field of its own and does not come from here. Adding a
name to the list is a decision; leaving one off costs nothing until something
tries to read it, and then fails loudly in a test rather than quietly in
production.

Outgoing has no BCC at all: `mail.mail` has no field for one, so nothing can
put one on the wire. That is pinned by a test asserting the built MIME carries
no `Bcc` header and that the SMTP envelope equals To + CC, so a future "add BCC
support" argues with a failing test instead of landing quietly. The
recommendation on that ticket, in advance: don't. Someone who needs to blind-
copy sends from Outlook.

### Block list

`res.partner.x_email_sync_blocked` excludes a contact from all mailbox sync,
regardless of routing settings. It is treated as an objection to processing:
blocked mail is skipped and **not** recorded in the triage queue.

---

## 4. Thread matching

"Where does this reply belong?" is a separate model from the fetcher —
`pan.mail.matcher` — because it is the decision that goes wrong most visibly and
the one worth testing without a provider, an HTTP mock or a mailbox.

Rules run strongest first; the first one at or above `AUTO_ROUTE_CONFIDENCE`
(0.8) wins and the ladder stops:

| # | Rule | Conf. | Basis |
|---|------|-------|-------|
| 1 | `odoo_headers` | 1.0 | `X-Odoo-Model` / `X-Odoo-Record-Id` |
| 2 | `references` | 1.0 | `In-Reply-To` + the full `References` chain |
| 3 | `thread_link` | 0.9 | (provider, **mailbox**, thread id) |
|   | `thread_link_legacy` | 0.85 | unscoped `mail.message.x_provider_thread_id`, read-only since 19.0.6.0.0 |
| 4 | `subject_participants` | 0.5 | normalised subject + same partner — proposal only |

Rules 1 and 2 are RFC 5322, so they behave identically on Microsoft 365, Gmail
and IMAP. Rule 3 is the only provider concept, and it is treated as *a hint
valid only inside one mailbox* — which is what a `conversationId` or `threadId`
actually is. Below the threshold `match()` returns candidates but leaves `model`
empty, so a caller can branch on `model` alone and never route on a guess.

Three things this changed, each a silent misroute before:

- **The chain, not one hop.** Only `In-Reply-To` was read; a client that sets
  just `References` fell through to the conversation-id lookup.
- **Newest, not oldest.** The conversation lookup ordered `id asc`, so replies
  threaded onto whatever record *first* touched the conversation — usually an
  old contact chatter post rather than the open ticket.
- **Scoped, not global.** A thread id was matched across every mailbox at once.

### One conversation, one thread

A conversation in the mailbox maps to exactly one record in Odoo, whatever the
recipient list. A mail addressed to two customers is logged once, on the record
the ladder picks, with both people in that one chatter — not once per
recipient.

`UNIQUE(provider, mailbox_id, thread_id)` on `pan.mail.thread.link` already
enforces this, so the rule costs nothing to keep. Logging per recipient instead
would need the record added to that key, a matcher returning a set rather than
one record, and dedup keyed per record instead of globally.

What it gives up: the second customer has no copy in their own file, and a
conversation that opens as a question on a lead and ends as an order stays on
the lead while the order's chatter is empty. Both match what Odoo itself does
with a mail thread, so neither is a surprise to a user.

### The two indexes

Neither of these could be a Char field on `mail.message`.

`pan.mail.message.ref` maps an RFC 5322 Message-ID onto the `mail.message` it
belongs to. One Odoo message legitimately has *several* Message-IDs: the one
Odoo generated, the one the provider assigned on the wire (Graph mints its own
`internetMessageId` and gives no way to override it), and any id a forwarding
client re-used. A single Char holds one of those; a `References` chain has to
resolve all of them.

`pan.mail.thread.link` maps a provider thread handle onto an Odoo record,
*scoped to the mailbox that saw it*. Provider thread ids are not global:
Microsoft's `conversationId` is mailbox-local and derived from the conversation
topic, Gmail's `threadId` is account-local, and two mailboxes syncing the same
exchange see two different ids for it.

Neither model is provider-specific. IMAP has no thread handle at all; the
matcher synthesises one from the root of the `References` chain and stores it
here like any other.

These two are the *only* places a wire id is stored. `mail.mail` used to keep
the provider's Message-ID and thread handle as well, and `mail.message` the
Message-ID: three copies of one fact, one of them on a row Odoo deletes after
sending. 19.0.6.0.0 moved the surviving values into the ref index and dropped
the columns. One column stays, `mail.message.x_provider_thread_id`, because
its rows carry no mailbox and cannot be moved into the scoped link; nothing
writes it any more and the legacy rule reads it until those threads pass the
age limit.

---

## 5. Email flows

### Outgoing

```
User clicks "Send"
      │
      ▼
mail.compose.message — x_send_from_mailbox_id = selected mailbox
      │
      ▼
mail.mail._resolve_route()
  One answer, in this order:
    1. internal notification → notifications@
    2. the composer's choice
    3. the author's default mailbox
    4. no author at all → notifications@
  Anything unanswerable raises RoutingError and the mail FAILS.
  It is never rerouted to a different sender.
      │
      ▼
mailbox._get_client().send_message(mail, mailbox, account, reply_context)
      │
      ├── microsoft.graph.client → draft → send (see below)
      ├── google.gmail.client    → MIME via mime_utils → users.messages.send
      └── imap.smtp.client       → MIME via mime_utils → SMTP, then APPEND to Sent
```

**Why Graph uses draft → send and not `sendMail`.** `sendMail` returns nothing.
The two ids we need only exist on a created draft:

1. `POST /users/{email}/messages` → returns `internetMessageId` and `conversationId`
2. `POST /users/{email}/messages/{id}/send`

`internetMessageId` prevents the Sent Items sync from re-importing our own mail,
and `conversationId` is the thread handle. For a reply, step 1 becomes
`createReply` on the parent's provider id, then a PATCH — see §6.

Both steps are separately permissioned, which is why §10 lists two pairs:
creating the draft is `Mail.ReadWrite`, sending it is `Mail.Send`.

| Mailbox type | Draft endpoint | Required permissions |
|------|----------------|----------------------|
| Personal | `/users/{email}/messages` | `Mail.ReadWrite` + `Mail.Send` |
| Shared | `/users/{email}/messages` | `Mail.ReadWrite.Shared` + `Mail.Send.Shared` + SendAs in Exchange |
| Notification | `/users/{email}/messages` | `Mail.ReadWrite.Shared` + `Mail.Send.Shared` + SendAs in Exchange |

### Incoming (polling)

```
Cron (every 1 min) → _process_mailbox(mailbox)
      │
      ▼
client.fetch_messages(folder, since_datetime, limit)
  ascending by date, up to 200 per folder
      │
      ▼
Pre-filters: duplicate, Odoo-originated, internal domain, block list, sync mode
  (skips that a customer might want to reverse → pan.mail.item; see §7)
      │
      ▼
pan.mail.matcher.match(message, mailbox, partner)
      │
      ├── model set  → message_post onto that record          → outcome 'threaded'
      ├── sent item  → message_post onto the correspondent    → outcome 'sent_item'
      └── otherwise  → _route_email_via_alias()
                         ├── alias configured → message_new() → outcome 'created'
                         └── no alias → contact chatter       → outcome 'fallback'
      │
      ▼
pan.mail.routing.log row written; cursor advanced
```

Every `message_post` carries the provider's own `date`, so a historical import
keeps the timeline instead of collapsing onto the day the import ran.

### Cursor

Ascending sort plus an incremental cursor, the pattern Odoo fetchmail and
Stripe webhooks use:

1. Fetch up to 200 messages per folder, oldest first, since `last_sync_date`
2. Advance `last_sync_date` to the **minimum** of the two folders' latest
   message, so nothing is skipped in the slower folder
3. If nothing came back at all, the cursor jumps to `now()` — caught up

`sync_start_date` is user-configurable (default: now). Moving it earlier
resets the cursor, which is how a historical import is started. Duplicates are
skipped on Message-ID, so a re-run is safe.

---

## 6. Outgoing threading

Threading is half a *send* problem. `mail.mail._build_reply_context()` builds a
provider-neutral hint from Odoo data — `in_reply_to`, `references` (root first),
`thread_id`, `provider_message_id` — and each client uses what it can honour:

| | Microsoft 365 | Gmail | IMAP/SMTP |
|---|---|---|---|
| Standard headers | refused (`internetMessageHeaders` takes `x-` only) | set on the MIME | set on the MIME |
| How it threads | `createReply` on `provider_message_id`, then PATCH | `In-Reply-To` + `References` + `threadId` | `In-Reply-To` + `References` |
| When it can't | plain draft, unthreaded | plain send, unthreaded | plain send, unthreaded |

The two MIME senders share `providers/mime_utils.build_message()`, so the
headers are written once. It prefers `reply_context` over `mail.mail.references`
on purpose: Odoo's field holds the ids *Odoo* generated, while the id that went
on the wire is the one `new_message_id()` minted. A chain built from Odoo's ids
names messages the recipient never saw.

The Message-ID we emit is the one the *recipient saw* — Graph mints its own
`internetMessageId` on send, so `pan.mail.message.ref` is consulted first.
Gmail refuses a `threadId` whose message is not a valid RFC reply, so the handle
is only claimed when `In-Reply-To` is there to justify it.

**Providers with no thread concept.** IMAP/SMTP supply no thread handle, so both
sides synthesise one from the root of the `References` chain — the matcher on
receive, `mime_utils.thread_key()` on send. Every participant in a thread
carries the same root, so rule 3 keeps working without the provider offering
anything.

That root is also why a long chain is trimmed from the *middle*: past
`REPLY_CHAIN_LIMIT`, `_build_reply_context()` keeps the root plus the nearest
ancestors. Capping the walk instead would drop the root — and hand the same
conversation a new thread id partway through, exactly the drift rule 3 exists
to prevent. (RFC 5322 recommends the same shape for a truncated References.)

---

## 7. Visibility

Better matching does not tell anyone where mail landed — it only makes the
answer right more often. Three models answer three different questions.

### `pan.mail.routing.log` — where did this mail go?

One row per delivered mail (Settings → Technical → Email → Mail Routing) with
the rule, the confidence, and every candidate the ladder rejected.

`outcome` separates three things that look identical from inside Odoo:
`threaded` onto something that existed, `created` something new, `fallback` to
contact chatter. `needs_review` flags exactly two of them:

- **fallback** — delivered, but to a place nobody is looking.
- **created *with* candidates** — the expensive, silent one: we may have opened
  a duplicate ticket for a conversation that was already running.

A `threaded` mail and our own `sent_item` never flag. A queue that cries wolf on
every routed mail gets ignored, and then it may as well not exist.

Deliberately a record of what happened, **not** a queue that holds mail back.
Delivery is unchanged. A log that is wrong costs a confusing row; a queue that
is wrong costs a customer an answer. A daily cron drops rows past
`pan_mail_pro.routing_log_retention_days` (default 90) unless still flagged.

### `pan.mail.item` — what reached Odoo but landed nowhere?

`_process_message()` used to drop mail in five places with nothing but a log
line. Three of those are correct and final; two are a decision the customer
would want to see and possibly reverse. Two rules shape the model:

**Nothing is queued that was filtered on purpose.** A blocked contact is an
objection to processing, and storing that mail in a new table inverts what the
flag means. Mail between internal users has no document context and no document
ACL to inherit. Internal-domain mail was excluded by configuration. None of
these become records here — not even their metadata.

**No body, no attachments.** The provider stays the source of truth; the body is
re-fetched when someone opens the item. That keeps the table small, keeps a
second copy of every email out of the database, and keeps the erasure surface to
metadata that expires on its own. Above `MAX_PENDING_PER_MAILBOX` (50 000)
pending items recording stops — a queue nobody works is a disk-space bug.

### `pan.mail.coverage` — is any of this working?

The number that says whether the "two separate worlds" problem is being solved,
and the gate on building more triage: if almost everything files itself
correctly, a queue solves nothing and the effort belongs elsewhere.

Measured inside Odoo and nowhere else. Sending usage telemetry out would
contradict the module's own data-disclosure statement. A `TransientModel`
rather than a stored report: this is a question you ask, not a history you keep,
so nothing is written and the answer cannot go stale.

It reads the **communication lens** fields on `mail.message` (`x_direction`,
`x_mailbox_id`, and the document pointer made groupable). Odoo links a message
to its document through `model` (a char) and `res_id` (an int) — a pointer with
no foreign key, which cannot be grouped or clicked through — and `mail.message`
is one table for notes, emails, system logs and chats. "Where did this email end
up?" has no answer in standard Odoo. These fields give it one, with partial
indexes so the index stays off the note and log rows that are the vast majority.

---

## 8. The AI seam

AI is a second seam shaped exactly like the provider seam, for the same reason:
one abstract contract (`pan.mail.ai`), a registry, and a rule that only an
implementation knows what a vendor's API looks like. Three properties are
enforced by tests and by CI greps, not by convention:

- **Opt-in by data.** `none` is a real backend that returns nothing. An
  unconfigured database behaves as though the feature were absent.
- **AI cannot block mail.** It is never called from `mail.mail.send()` or
  `_process_message()` — those run in a one-minute cron inside a savepoint,
  where a twenty-second model call would stall a mailbox and a failure would
  roll the message back. A separate cron enriches records that already exist.
- **AI may rank, never invent.** The candidate shortlist is built by
  deterministic matching; a suggestion naming anything else is discarded.

Bring-your-own-key: the call goes from the customer's Odoo straight to the
provider. Pantalytics never proxies it, which is what keeps the manifest's
data-disclosure statement true and keeps Pantalytics out of every customer's
processor chain. Only an envelope is sent — subject, sender, recipient, date and
a shortlist of candidate record names — never a body or an attachment.

**Why AI is absent from matcher rules 1–3.** A `References` chain is exact, free
and reproducible, and a language model would make a solved problem
probabilistic. The ambiguous residue — a customer who starts a fresh mail
instead of replying, a known contact with three open tickets — is where it earns
its place, and it plugs in as one more rule by overriding `_match_rules()`.
Running last means it is only ever asked about mail the deterministic rules
could not place, which is what keeps it affordable on a one-minute cron.

Auto-routing stays shut behind the `routing_smart` constraint until there is
evidence from real suggestions that it should open.

**Not shipped yet.** The seam exists; the feature does not. The settings page
has no AI section, so `pan_mail_pro.ai_backend` stays at `none` unless somebody
writes the config parameter by hand, and the enrichment cron ships inactive.
That is deliberate: setup is the thing to get right first, and a configurable
half-feature is a support burden with no user. Putting the section back is a
view change, not a rewrite.

---

## 9. Design decisions

### 9.1 Token encryption

Fernet symmetric encryption with an auto-generated key in `ir.config_parameter`
(`pan_mail_pro.encryption_key`), because Odoo.sh does not support custom
environment variables and the database is already encrypted at rest. Zero
configuration, and defense-in-depth against SQL injection and backup leaks. All
encryption goes through `models/encryption_utils.py`.

### 9.2 Polling over webhooks

Works out of the box on Odoo.sh with no public endpoint, reuses the existing
OAuth infrastructure, and a one-minute delay is acceptable for email.

### 9.3 Native `message_new()` for incoming mail

This is exactly what Odoo's standard SMTP gateway uses. It handles record
creation plus the initial post correctly, triggers auto-replies properly, and —
critically — does **not** send duplicate follower notifications to the sender.

```python
# CORRECT — uses Odoo's native flow
record = self.env['helpdesk.ticket'].message_new(msg_dict, custom_values={'team_id': team.id})

# WRONG — triggers unwanted notifications
record = self.env['helpdesk.ticket'].create(vals)
record.message_post(body=body, ...)  # Sender gets notified!
```

### 9.4 Pre-create partners

Find or create the partner *before* posting. Odoo's own auto-creation sometimes
takes the partner name from the email subject; pre-creation gets it from the
`From` header.

### 9.5 No fallback between senders

A mail that cannot be sent from the mailbox it was addressed from **fails**,
with a reason, and waits in the queue. It is not quietly re-sent from
`notifications@`. Silent rerouting looks like success and puts the wrong address
in front of a customer. Other mails in the same batch are unaffected.

Corollary, learned the hard way: raising and recording are mutually exclusive in
one transaction — the raise rolls the write back. `mail.mail.send()` picks which
one the caller gets, and the cron's `auto_commit` pass a minute later supplies
the other.

Second corollary, learned harder: **that rollback is not selective.** It also
unwinds `state='sent'` on every mail in the batch the provider had already
delivered, so Odoo forgets it sent them and the queue sends them again. Telling
the sender is worth a lost `failure_reason`; it is not worth mailing a customer
twice. So `send()` raises only when a rollback costs nothing — the queue is
driving, or nothing in the batch went out — or when the caller passed
`raise_exception` and owns the trade-off.

That does not make a mixed batch silent. `_fail()` marks the `mail.notification`
rows through Odoo's own `_postprocess_sent_message`, which is what draws the red
"message not sent" marker and its retry button in the chatter. The author finds
out on the record it failed on, which is a better place than a dialog anyway —
and it is Odoo's mechanism, not a second one of ours.

### 9.6 IMAP/SMTP: what the protocols make the contract absorb

Three protocol facts had to land somewhere, and all three land inside the client:

| Fact | Consequence |
|------|-------------|
| No OAuth | `account_is_connected()` is a provider question. For IMAP it means host + login + password; the OAuth-shaped contract methods refuse with an explanation instead of pretending. |
| UIDs are folder-scoped and invalidated by `UIDVALIDITY` | `provider_message_id` is `folder:uidvalidity:uid`. A renumbered folder is refused rather than misread — a bare UID would fetch a different message. |
| No thread id | The root of the `References` chain is the thread key, so every message in a conversation shares one handle. |

Two smaller ones: `SEARCH SINCE` is date-granular and server-local, so the
cursor is asked wide and narrowed in Python; and SMTP files nothing in Sent, so
the client APPENDs the sent copy itself — best-effort, because the mail is
already delivered and failing to file a copy is not a send failure. The APPEND
is conditional: the folder is probed for the Message-ID first, so a host that
files its own copy on submission gets one copy, not two (a failed probe counts
as absent — a duplicate beats no copy).

Outgoing size and time are the server's numbers, not ours: the SIZE extension
(RFC 1870) from the EHLO reply refuses an oversized mail *before* the upload
with a reason the sender can act on, and the SMTP timeout scales with the
payload — a flat timeout silently demands a fast uplink for a large attachment.
Both mirror the same decisions in `squirrel-mcp`'s SMTP client, which shares
this provider shape.

### 9.7 Graceful degradation: opt-in by data

As long as **no `pan.mail.mailbox` records exist**, `mail.mail.send()` falls
through to `super().send()` and Odoo's standard SMTP queue handles outbound
mail. Demo, QA and dev databases keep working before any provider is wired up.
Once an admin creates the first mailbox, provider routing activates and
unroutable mail is failed rather than leaked via SMTP.

Creating that first mailbox is also what disables SMTP
(`_activate_smtp_takeover`). It used to happen in the install hook, which
contradicted the paragraph above: a freshly installed database could not send at
all — not through Mail Pro, not configured yet, and not through SMTP either. The
one thing an admin needs to send at that moment is user invitations, and those
were exactly what died.

One window survives by design: the first mailbox exists (routing on, SMTP off)
but `notifications@` is not connected yet. Internal notifications are **queued**
in that window rather than cancelled, and go out by themselves once the
notification mailbox works. See `_is_awaiting_notification_mailbox`.

### 9.8 Configuration that is not configuration

The Microsoft auth and token URLs were config parameters. They are the same for
every tenant, and a wrong value is unrecoverable from the UI. They are
constants. A knob nobody should turn is a way to break the product from the
settings page.

### 9.9 Neutralization: a staging copy must not mail customers

Odoo protects a database copy by *neutralizing* it: every installed module's
`data/neutralize.sql` runs, base deactivates every `ir_mail_server` and inserts
an invalid one, all crons stop, and `database.is_neutralized` is set.

That protection is SMTP-shaped, and Mail Pro is not. It calls the Graph API,
the Gmail API or its own SMTP host with credentials the dump still carries, so
a restored staging database would mail real customers from the real address —
and pass every check Odoo has, because it never asked `ir_mail_server` for
anything.

So the module neutralizes itself. `database_is_neutralized()` lives in
`models/neutralization.py`, below both encryption and the provider contract
because both depend on it, and it is asked in three places:

**Once, at rest.** `data/neutralize.sql` deactivates every mailbox and *removes*
the OAuth tokens and mailbox passwords. Same reasoning as base wiping
`smtp_pass`: a neutralized database gets copied around, and a dump carrying a
live refresh token can send from anywhere it lands. Odoo finds it by path, so it
is deliberately not listed in `__manifest__.py`.

**Always, at the credential funnel.** `encryption_utils.decrypt_value()` returns
empty. Every credential the module owns is read through that one function —
access tokens, refresh tokens, IMAP/SMTP passwords, both providers' client
secrets — so nothing can authenticate anywhere, including through call sites
nobody has written yet. It returns empty rather than raising because "no
credentials" is a state every caller already handles, and it leaves the account
form readable in staging.

**Always, before the network.** An empty credential fails at the *far* end: the
connection still opens and the provider still rejects it, once per attempt. So
each client also calls `_refuse_when_neutralized()` at the one point its
transport cannot avoid — `get_valid_token` and `_exchange_code_for_tokens` for
an OAuth provider, `_require_credentials` for a password one. Nothing leaves the
database at all, and re-authorizing in staging cannot write live credentials
back in. `tests/test_provider_contract.py` holds a new provider to the same
rule.

**Where a sentence is owed.** Routing an outgoing mail, the sync cron and
"Sync Now" each ask directly, so the refusal says *neutralized* instead of
"account not connected". Only a caller that knows what it was attempting can
say why it stopped.

Outgoing mail is *refused*, not dropped: the reason lands on the mail and it
stays queued, so nothing is lost if the database turns out to be the real one.

---

### 9.10 An imported message notifies nobody

An imported mail has already reached its recipients through the provider. Odoo
sending it again is wrong in every variant and for every mix of recipients,
which is what makes this a boundary rather than a filter: there is no
legitimate exception to argue about.

The rule is one override. Every post the sync makes carries
`pan_mail_fetcher.IMPORT_CTX`, and `mail.thread._notify_thread()` returns no
recipients for anything holding its `pan_mail_imported` flag.

The discriminator is a context flag rather than a field, and that is a
deliberate correction. `x_mailbox_id` looks like the natural answer and is
wrong: `mail.mail._record_sent()` stamps the same field on mail Odoo itself
sent, so a boundary keyed on it would conflate the two directions of one
mailbox and eventually silence a message a person wrote from the chatter. The
flag says exactly what is being asked — is this post an import — and is set in
one place. Its cost is being invisible afterwards and easy to drop in a
refactor, which is what `tests/test_sync_sends_nothing.py` exists to catch.

A field could not have carried it in any case: Odoo 19's
`_raise_for_invalid_parameters()` rejects field names it does not recognise as
`message_post()` arguments, so the lens fields are still written just after the
post. They describe how the mail arrived; they do not gate anything.

Two earlier attempts at the same goal failed silently, and the override
replaces both. `incoming_email_to` and `incoming_email_cc` were handed to
`message_post()` as keyword arguments Odoo discards, so the suppression their
comment described never ran at all. And `mail_create_nosubscribe` was set on the
`message_new()` path but not on the three partner-chatter posts, so the sync
subscribed the author of every mail it imported — which on the sender's own
contact card means a contact following itself and receiving its own
correspondence back by mail.

The sync never creates a follower. A follower is a human act.

Ingestion filters (§3) shrink what enters; this rule makes safe what did enter.
Neither substitutes for the other, and a mail with an external counterpart —
most mail — is only reached by this one.

### 9.11 A terminal outcome is recorded in one story, not two

When a mail cannot go out, two tables record it: `mail.mail.state` and the
`mail.notification` rows pointing at the same message. They must agree, and the
one that has to be right is the notification, because `mail.mail` is
garbage-collected and `mail.notification` is not. The table still standing
later is the one anybody reading the database, or the chatter, believes.

The cancel path used to write `state = 'cancel'` and stop there, leaving the
notifications at `ready` — "queued, not sent yet" — permanently. At one
customer, seventeen rows from one sync run still read `ready` eleven days
after their mails were cancelled: the chatter showed mail as pending that no
longer existed. `mail.mail._cancel_notifications()` closes that, taking the
value from Odoo's own `_get_notification_status()` so the two cannot drift.

Failures already worked this way, through `_postprocess_sent_message`. Cancels
were the one terminal outcome that did not.

### 9.12 Internal mail is always filtered

Mail between the company's own domains is never synced into Odoo. There is no
global setting and no per-mailbox toggle, and the two that existed —
`sync_internal_email` and `exclude_internal` — were removed in 19.0.6.4.0
rather than defaulted off.

A safety control with a switch is a safety control someone will flip. Both
switches were reachable from a settings page, neither was reversible in effect
(mail copied into a record stays there), and the failure they guard is a data
leak: a colleague's confidential thread readable by everyone with access to the
record. A default protects the databases nobody touched; removing the switch
protects all of them.

It also removed a question the product had no business asking. "Do you want
your internal email in Odoo?" reads like a preference and is not one — the
right answer is the same for every customer, and the customer cannot see the
consequence of the wrong one until it is in the database.

**The case being dropped**: a team mailbox where internal forwarding should be
logged, e.g. support@ receiving a colleague's forward of a customer complaint.
That thread now stops at the forward. The customer's own mail to support@ is
still logged, so the record is not empty, only shorter. One real workflow for
one setting that could quietly leak every internal thread in the database is
not a trade worth keeping.

Note the asymmetry that makes this cheap: a mail with *any* outside recipient
is correspondence and is still logged. "Internal" means every party is ours.

## 10. Security and permissions

All Microsoft permissions are **delegated** (user context, never application) —
this module has no admin-level access to a tenant.

| Permission | Purpose |
|------------|---------|
| `openid` / `profile` / `email` | OAuth login and the identity that just consented |
| `offline_access` | Refresh tokens |
| `User.Read` | Basic profile during OAuth |
| `Mail.ReadWrite` | Create drafts, read Sent Items |
| `Mail.Send` | Send from personal mailbox |
| `Mail.ReadWrite.Shared` | Create drafts in a shared mailbox |
| `Mail.Send.Shared` | Send from a shared mailbox |

The four Graph permissions are one list, requested in
`graph_client.get_authorization_url()` and repeated in the setup guide, the
manifest and `docs/security.md` — and they have to stay one list, which
`tests/test_microsoft_provider.py` now pins. A token carries the scopes that
were *requested*, so a permission granted in the Azure portal and left out of
the request simply is not there. The `Mail.Send` pair was missing from the
request until 19.0.5.1.0: sending worked wherever an admin had granted it
tenant-wide and 403'd where consent was incremental. **Accounts connected
before that version keep the old token and do not gain the scope**; they pick
it up the next time the user authorizes.

`openid`, `profile` and `email` are OIDC scopes rather than Graph permissions,
so they belong in the request and not in the Azure API-permissions list — which
is why the guides show four entries where this table shows seven.

For shared mailboxes users also need **SendAs** in the Exchange Admin Center.

| Aspect | Implementation |
|--------|----------------|
| Authentication | OAuth 2.0 (Microsoft Entra ID, Google) — or login + password on IMAP |
| Token storage | Encrypted at rest (Fernet) |
| Token refresh | Automatic |
| Data egress | Provider APIs only. Nothing goes to Pantalytics |
| AI | Off by default; customer's own key; envelope only, never bodies |

---

### The takeover is one-sided

`_activate_smtp_takeover()` disables every active `ir.mail_server` when the
first mailbox is created, which is the moment Mail Pro can actually deliver
rather than the moment it is installed. There is no counterpart for incoming:
the string `fetchmail` does not appear anywhere in the module.

An inbound server left enabled means Odoo fetches mail itself and routes it
through `mail.alias`, past every control in §3. Closing the outbound door and
leaving the inbound one open is not a smaller version of the same act; it is
the half that lets mail in.

## 11. Conventions

### Field naming

Fields added to *Odoo's own* models take the `x_` prefix per Odoo.sh guidelines
(`x_pan_mail_connected`, `x_send_from_mailbox_id`). Fields on the module's own
models do not — `pan.mail.account.refresh_token` and `pan.mail.mailbox.provider`
are plain, because the model is ours. See the vocabulary in §1 for the rest of
the naming rules.

`res.users` carried `x_microsoft_access_token` and four siblings as proxies onto
`pan.mail.account` until **19.0.5.0.0 removed them**. Every caller had been
rewritten; only tests still read them. A compatibility shim outlives its callers
silently — nothing fails when one goes stale.

### Custom email headers

Added to outgoing mail, and read back by the loop guard and matcher rule 1:

| Header | Example | Purpose |
|--------|---------|---------|
| `X-Odoo-Model` | `sale.order` | Source model |
| `X-Odoo-Record-Id` | `123` | Source record id |
| `X-Odoo-Mail-Id` | `456` | `mail.mail` record id |
| `X-Odoo-Message-Id` | `789` | `mail.message` record id |

### Log tags

| Tag | Purpose |
|-----|---------|
| `[Outgoing Mail]` | The send flow in `mail.mail`, any provider |
| `[Incoming Mail]` | The fetch flow in `pan.mail.fetcher`, any provider |
| `[Mail Matcher]` | Thread matching and the two indexes |
| `[OAuth]` | Authentication callbacks |
| `[Graph API]` / `[Gmail API]` / `[IMAP]` / `[SMTP]` | Inside one provider client only |
| `[Mail AI]` | The AI seam |
| `[Encryption]` | Credential encryption |
| `[Mail Pro]` | Setup, migrations, housekeeping |

---

## 12. Tests

29 files under `tests/`, roughly 7 500 lines. They fall into four groups:

| Group | Files | What they hold |
|-------|-------|----------------|
| Contracts | `test_provider_contract.py`, `test_ai_contract.py` | Every provider/backend answers the contract identically |
| Providers | `test_microsoft_provider.py`, `test_google_provider.py`, `test_imap_provider.py` | Wire-level behaviour per vendor |
| Pipeline | `test_incoming_sync*.py`, `test_incoming_mail.py`, `test_mail_matcher.py`, `test_routing_log.py`, `test_mail_item.py` | Fetch → filter → match → post |
| Sending & UI | `test_outgoing_*.py`, `test_compose_*.py`, `test_mailbox_*.py`, `test_setup_flow.py`, `test_onboarding.py` | Routing, threading, composer, permissions, onboarding |
| Migrations | `test_account_migration.py`, `test_rename_migration.py` | The scripts in `migrations/`, run against real rows |

`tests/common.py` provides the shared fixture — a notification mailbox, a shared
mailbox, a personal mailbox, connected users, an external partner, and a
`mock_graph` context manager that patches every outbound HTTP call.

The widest useful seam for pipeline tests is `_process_mailbox(mailbox)` with
only HTTP mocked: its signature survived the provider refactor, so the same
tests prove the refactor preserved behaviour rather than merely not crashing.

New tests use `@tagged('pan_mail_pro', 'post_install', '-at_install')` and
extend `TransactionCase`. See [CLAUDE.md](CLAUDE.md) for how to run them and
what CI enforces.

---

## 13. Known limitations

**SendAs permissions cannot be queried.** Microsoft Graph offers no endpoint to
ask which shared mailboxes a user may send as
([known limitation](https://learn.microsoft.com/en-us/answers/questions/1168052/)).
So the module cannot show only accessible mailboxes: an admin adds them
manually, and Azure validates at send time.

**IMAP `SEARCH SINCE` is date-granular**, so the cursor is asked wide and
narrowed in Python — see §9.6.

---

## References

- [Microsoft Graph: send mail from another user](https://learn.microsoft.com/en-us/graph/outlook-send-mail-from-other-user)
- [Odoo.sh environment variables FAQ](https://www.odoo.sh/faq#what-are-the-default-environment-variables)
- [Fernet encryption](https://cryptography.io/en/latest/fernet/)
