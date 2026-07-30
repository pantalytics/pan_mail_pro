# Claude Code Context

Project context for Claude Code AI assistant.

## Module Overview

**pan_mail_pro** - Microsoft 365, Google Workspace and IMAP/SMTP email
integration for Odoo 19.0 Enterprise Edition.

Send and receive emails via the Microsoft Graph API, the Gmail API (both OAuth
2.0 delegated) or plain IMAP/SMTP with a server, login and password. The module
name still says Outlook; the rename to `pan_email_pro` is a separate phase.

## Development Principles

1. **Odoo 19 Compatibility** - Before every commit, verify code meets Odoo 19 requirements
2. **Minimal Footprint** - Stay as close to standard Odoo as possible
3. **Code Discipline** - Push back on feature requests that cause unnecessary bloat
4. **Lean Implementation** - Prefer configuration over code, reuse Odoo native features

### Odoo 19 Checklist (verify before commit)
- [ ] No `attrs` in views → use `invisible`, `readonly`, `required` directly
- [ ] No `numbercall` on cron jobs (deprecated)
- [ ] Stored computed fields have `@api.depends` decorator
- [ ] Use `groups` attribute for field access control
- [ ] XML ids follow pattern: `module_name.record_name`
- [ ] Bump version in `__manifest__.py` (format: `19.0.X.Y.Z`)

## Key Files

| File | Purpose |
|------|---------|
| `models/mail_provider_client.py` | Provider-agnostic client contract + registry |
| `models/mail_mail.py` | Outgoing email override (routes via provider client) |
| `models/mail_message.py` | Microsoft message ID storage for threading |
| `models/mail_compose_message.py` | Composer "Send From" dropdown + setup warning |
| `models/mail_alias.py` | Cleaner alias display (name only, no domain) |
| `models/microsoft_mailbox.py` | Mailbox configuration + routing rules |
| `models/pan_mail_account.py` | Credentials for one address on one provider |
| `models/providers/microsoft/graph_client.py` | Microsoft 365 implementation of the contract |
| `models/providers/google/gmail_client.py` | Gmail implementation of the contract |
| `models/providers/imap_smtp/imap_client.py` | IMAP/SMTP implementation of the contract |
| `models/providers/mime_utils.py` | Outgoing MIME, shared by the two MIME senders |
| `models/pan_mail_fetcher.py` | Incoming email sync (uses `message_new()`) |
| `models/pan_mail_matcher.py` | Thread matching: which Odoo record does this mail belong to |
| `models/pan_mail_thread_index.py` | The two indexes the matcher reads (Message-IDs, thread→record) |
| `models/pan_mail_routing_log.py` | Where each incoming mail landed and why (+ review queue) |
| `models/pan_mail_internal_domains.py` | Internal domain list + the fail-closed gate on incoming sync |
| `models/res_partner.py` | Contact block list field |
| `controllers/main.py` | OAuth callback handlers (Microsoft + Google) |
| `tests/test_provider_contract.py` | Guards the contract seam itself |
| `tests/test_incoming_mail.py` | Unit tests for incoming mail processor |
| `tests/test_mail_matcher.py` | Unit tests for the matching ladder |
| `tests/test_imap_provider.py` | IMAP/SMTP client (fake imaplib/smtplib, no sockets) |

## Provider Architecture

The module supports multiple email providers through one interface, shaped by
what Odoo needs rather than by any single provider's API:

```
Odoo (mail.mail, mail.thread, res.users)
    ↓
mail.provider.client                      ← the contract
    ↓
microsoft.graph.client / google.gmail.client / imap.smtp.client   ← implementations
    ↓
pan.mail.account                          ← credentials, one per address+provider
```

**The rule:** nothing outside a provider implementation may build provider URLs,
import provider SDKs, or reason about provider-specific payload shapes.
Everything crossing the boundary uses the normalized message / attachment /
send-result shapes documented in `models/mail_provider_client.py`.

Provider implementations live under `models/providers/<vendor>/`, so the
boundary is a directory you can grep rather than a convention you have to
remember. There is exactly **one** layer: the client *is* the contract
implementation. An earlier branch had a separate `pan.mail.provider.*` adapter
in front of each client; it did nothing the client could not do itself and was
dropped in the merge.

Credentials do not cross the boundary either. A provider is handed a
`pan.mail.account` and is the one that decides *which* account applies —
`resolve_sending_account()` / `resolve_receiving_account()` — and *whether they
are usable* — `account_is_connected()`, which is a refresh token for OAuth
providers and a host + login + password for IMAP. That is where providers
genuinely diverge.

### Adding a provider

1. Add the code to `PROVIDER_CLIENTS` and `PROVIDER_SELECTION` in `mail_provider_client.py`
2. Create `models/providers/<vendor>/<name>_client.py` with
   `_inherit = 'mail.provider.client'` implementing the contract
3. Declare its capabilities (`supports_shared_mailbox`, `supported_mailbox_types`)
4. Add an ACL row in `security/ir.model.access.csv`
5. If it does not use OAuth, override `account_is_connected()` — the default
   answer is "has a refresh token", which no password provider ever will

The same code is used by `x_microsoft.mailbox.x_provider` **and**
`pan.mail.account.provider` — both read `PROVIDER_SELECTION`, so an account and
the mailbox it serves can never disagree about the provider's name.

No call site outside the client changes. `tests/test_provider_contract.py` covers
the seam; a new provider must satisfy the same assertions.

### Capability differences

Providers disagree about sending as somebody else, so `resolve_sending_account()`
is the client's job:

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

## Thread Matching

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
|   | `thread_link_legacy` | 0.85 | unscoped `x_microsoft_conversation_id` |
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

### Outgoing threading

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
Emitting Odoo's own id would produce a chain that does not match the one coming
back. Gmail refuses a `threadId` whose message is not a valid RFC reply, so the
handle is only claimed when `In-Reply-To` is there to justify it.

**Providers with no thread concept.** IMAP/SMTP supply no thread handle, so both
sides synthesise one from the root of the `References` chain — the matcher on
receive, `mime_utils.thread_key()` on send. Every participant in a thread
carries the same root, so rule 3 keeps working without the provider offering
anything.

### Routing log

Better matching does not tell anyone where mail landed — it only makes the
answer right more often. `pan.mail.routing.log` writes one row per delivered
mail (Settings → Technical → Email → Mail Routing) with the rule, the
confidence, and every candidate the ladder rejected.

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

**Adding AI.** Deliberately absent from rules 1-3: a `References` chain is exact,
free and reproducible, and a language model would make a solved problem
probabilistic. The ambiguous residue — a customer who starts a fresh mail
instead of replying, a known contact with three open tickets — is where it earns
its place, and it plugs in as one more rule by overriding `_match_rules()`.
Running last means it is only ever asked about mail the deterministic rules
could not place, which is what keeps it affordable on a one-minute cron.

## Mailbox Types

| Type | Who sees it? | OAuth token used |
|------|--------------|------------------|
| Personal | Only owner | Owner's token |
| Shared | Everyone | Sender's own token |
| Notification | Everyone | Owner's token |

## Internal domains (fail-closed)

Incoming sync will not start until `pan.mail.internal.domains` has a list of the
company's own domains, or an admin has explicitly switched on "Sync internal
email". The gate exists in two places on purpose: a constraint on
`x_microsoft.mailbox` (you cannot save a syncing mailbox without it) and a check
in `_process_mailbox` (a list emptied later stops the sync and shows up as an
error on the mailbox).

This replaces reading `mail.alias.domain`, where an empty list meant "nothing is
internal" — a fresh install therefore synced every internal email into Odoo,
including confidential ones, and nothing said so. Alias domains now only feed
`suggest_domains()`.

## Graceful degradation

The module is opt-in by data: as long as **no `x_microsoft.mailbox` records exist** in the database, `mail.mail.send()` falls through to `super().send()` so Odoo's standard SMTP / mail queue handles outbound mail. This keeps demo, QA, and dev environments working before Azure is wired up. Once an admin creates the first mailbox, Graph routing activates; mails that can't be routed are then cancelled rather than leaking via SMTP.

Creating that first mailbox is also what disables SMTP (`_activate_smtp_takeover`). It used to happen in the install hook, which contradicted the paragraph above: a freshly installed database could not send at all — not through Mail Pro, which is not configured yet, and not through SMTP either. The one thing an admin needs to send at that moment is user invitations, and those are exactly what died.

One window survives by design: the first mailbox exists (routing on, SMTP off) but `notifications@` is not connected yet. Internal notifications are **queued**, not cancelled, in that window and go out by themselves once the notification mailbox works. See `_is_awaiting_notification_mailbox`.

## Development

Each addon repo can be tested independently with its own `.local/` directory. A shared Dockerfile lives in the parent folder since Odoo Enterprise source is shared.

### Directory structure
```
~/Documents/GitHub/
├── .docker/
│   └── Dockerfile               ← Shared Dockerfile (Enterprise + deps)
├── .dockerignore                 ← Limits build context to odoo-enterprise/
├── odoo-enterprise/              ← Odoo 19 Enterprise source (shared)
├── pan_mail_pro/              ← This repo
│   └── .local/                   ← Per-repo Docker config (gitignored)
│       ├── docker-compose.yml
│       └── odoo.conf
├── odoo-pantalytics/
│   └── .local/                   ← Per-repo Docker config
└── odoo-customer-goudsmit/
    └── .local/                   ← Per-repo Docker config
```

### Container filesystem
```
/opt/odoo/odoo-enterprise/       ← COPIED into image (rebuild needed for changes)
/mnt/extra-addons/               ← BIND MOUNT from host (live editing, no commit needed)
/var/lib/odoo/                   ← NAMED VOLUME (persistent filestore + sessions)
/etc/odoo/odoo.conf              ← BIND MOUNT from host (live editing)
```

### Volume mounts per repo

| Repo | Mount | Effect |
|------|-------|--------|
| pan_mail_pro | `..:/mnt/extra-addons/pan_mail_pro` | Single addon, direct from repo |
| odoo-pantalytics | `../addons:/mnt/extra-addons` | All addons via submodules |
| odoo-customer-goudsmit | `../addons:/mnt/extra-addons` | All addons via submodules |

Key config: `data_dir = /var/lib/odoo` in odoo.conf MUST match the volume mount in docker-compose.yml.

### Local Docker Setup
```bash
cd .local
docker-compose up -d             # Odoo at http://localhost:8069, db: test_db
```

### Restart after Python changes
```bash
cd .local
docker-compose restart odoo
```

### Upgrade module (apply model/view/data changes)
```bash
cd .local
docker-compose exec -T odoo python -m odoo -c /etc/odoo/odoo.conf -d test_db -u pan_mail_pro --stop-after-init
docker-compose restart odoo
```

### View logs
```bash
cd .local
docker-compose logs -f odoo
```

### Run unit tests
```bash
cd .local
docker-compose stop odoo
docker-compose run --rm odoo python -m odoo -c /etc/odoo/odoo.conf \
  -d test_db -u pan_mail_pro --test-enable --test-tags=pan_mail_pro --stop-after-init
docker-compose start odoo
```

**If it reports `0 tests`**, the module isn't installed in `test_db` — `-u` (update) silently does
nothing for an uninstalled module. Check with:
```bash
docker-compose exec -T db psql -U odoo -d test_db -c \
  "SELECT name,state FROM ir_module_module WHERE name='pan_mail_pro';"
```
If `uninstalled`, use `-i` instead of `-u` once, then `-u` works from then on.

### Rebuild image (only when Dockerfile or odoo-enterprise changes)
```bash
cd .local
docker-compose build odoo && docker-compose up -d
```

### Cloudpepper dev instance

| | |
|---|---|
| URL | https://mailpro-dev.cloudpepper.site |
| Server | `Pantalytics Demo` (Odoo 19.0 **community**), shared with the demo instances |
| Tracks | branch `19.0`, webhook + auto-upgrade on |
| Login | `admin` / secret `MAILPRO_ODOO_ADMIN_PASSWORD`, project `dev` in Bitwarden Secrets Manager |

Only `pan_mail_pro` and its dependencies (`mail`, `base`, `crm`) are installed, so
this is the closest thing to what CI builds — with a public HTTPS URL in front of it.

**What it is for:** OAuth. Azure and Google redirect URIs here have the same shape
as production, which `localhost:8069` never does. A push to `19.0` is live in about
a minute, so the round trip from merge to clicking through a real consent screen is
short.

**What it is not for:**
- **The test suite.** `--test-enable` is a local-Docker / CI thing; there is no way
  to run it against this instance. Nothing here replaces `docker-compose run`.
- **Helpdesk.** Odoo's `helpdesk` ships only in Enterprise, so on a community server
  the alias routing is unreachable: `x_route_to_team` on the mailbox, the
  `x_alias_id` link to `helpdesk.team`, and ticket creation through `message_new()`.
  `tests/test_incoming_mail.py` skips that class on a missing `helpdesk.team`, here
  as in CI. Test it locally against the Enterprise source — the third-party
  `helpdesk_community` addons are no substitute, because this code names
  `helpdesk.team` and `helpdesk.ticket` directly and those use their own models.

**Auto-upgrade only migrates when the manifest version moves.** Cloudpepper pulls
the code and runs `-u pan_mail_pro` on every push, but Odoo only executes migration
scripts when `__manifest__.py`'s version is *higher* than what `ir.module.module`
records. Python, view and asset changes land on the restart regardless; new fields
and data migrations need the version bump the Odoo 19 checklist already asks for.
Forget it and the instance quietly serves the old schema.

## CI/CD (GitHub Actions)

Three workflows in `.github/workflows/`:

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| `ci.yml` | every push + PR | lint (ruff), XML well-formedness, Odoo 19 checklist greps, manifest data-file check, version-bump check (PRs only), full test suite in a real Odoo (fresh install **and** upgrade from the last release) |
| `gitleaks.yml` | every push + PR | secret scan |
| `release.yml` | push to `19.0` | tags the merge commit `v<manifest version>` if that tag does not exist yet |

### How tests run

The `test` job installs the module into the **official `odoo:<series>` community
image** and runs `--test-enable --test-tags=pan_mail_pro` against a Postgres
service container. No Enterprise source and no Azure credentials are needed:
`mail`, `base` and `crm` all ship in community, and the Helpdesk tests skip
themselves when `helpdesk.team` is absent.

`sale` and `mass_mailing` are installed alongside the module even though they
are not dependencies. Nine tests skip themselves without them, and a skip does
not show up in the summary — so those paths were passing by not running. A
follow-up step asserts both are actually installed, because a skip that comes
back is otherwise invisible. `helpdesk` is Enterprise, so
`test_route_to_helpdesk` remains a real CI gap covered only by `TESTPLAN.md`.

Both test jobs end in `tools/ci_assert_tests.sh`, which fails the build when
Odoo reports no summary, any failure, **or zero tests** — the last one because
`0 failed, 0 error(s) of 0 tests` is otherwise a green build that verified
nothing.

The series is **derived from `__manifest__.py`**, not hardcoded: `19.0.1.3.0`
→ `odoo:19.0`. An addon repo carries one Odoo version per branch, so a future
`18.0` branch tests against Odoo 18 without editing the workflow.

Reproduce a CI run locally (this is exactly what the job does):
```bash
docker run --rm -v "$PWD:/mnt/extra-addons/pan_mail_pro:ro" \
  --entrypoint odoo odoo:19.0 -d ci_test \
  --db_host=<db> --db_user=odoo --db_password=odoo \
  --addons-path=/usr/lib/python3/dist-packages/odoo/addons,/mnt/extra-addons \
  -i pan_mail_pro --test-enable --test-tags=pan_mail_pro \
  --stop-after-init --without-demo=all --max-cron-threads=0
```

**Fresh install vs. upgraded database.** The `test` job installs fresh; the
`upgrade` job installs the newest `v<series>.*` tag that is not HEAD and then
runs `-u pan_mail_pro --test-enable` on that database. So the suite runs twice,
against both shapes, and the scripts in `migrations/` finally execute somewhere
other than a customer's database. Tests that touch columns of unstored fields
must still create those columns themselves — see
`tests/test_account_migration.py::_ensure_legacy_columns`.

The `pan_outlook_pro` → `pan_mail_pro` rename is *not* covered by that job: it
happens outside Odoo (`tools/rename_to_mail_pro.sql`), before the registry
loads, so no module upgrade can drive it. It stays a manual runbook.

## Working from the Claude Code mobile app

`19.0` is protected: no direct pushes, PR + green CI required. The full loop
from a phone:

```bash
git checkout -b feature/<name>
# ... changes ...
git commit -am "..."            # bump __manifest__.py version if code changed
git push -u origin HEAD
gh pr create --fill --base 19.0
gh pr merge --auto --squash --delete-branch
```

`--auto` is the part that makes this work without a desktop: GitHub merges the
PR by itself the moment CI turns green, so there is nothing to come back to.
Check status later with `gh pr checks` or `gh run watch`.

## Conventions

- All custom fields use `x_` prefix (Odoo.sh requirement)
- Log tags: `[Graph API]`, `[Incoming Mail]`, `[OAuth]`, `[Mail Matcher]`
- Use `invisible` instead of `attrs` in views (Odoo 19)
- Stored computed fields need `@api.depends` decorator

## Common Tasks

### Adding a new Graph API method
1. Add method to `microsoft_graph_client.py`
2. Use `get_valid_token(user)` for authentication
3. Handle errors with `_extract_graph_error()`

### Debugging email issues
1. Check Odoo logs for `[Graph API]` and `[Incoming Mail]` tags
2. Verify OAuth: user should have `x_microsoft_oauth_connected = True`
3. Check mailbox state: should be 'active'

## Key Design Decisions

### Use `message_new()` for incoming emails
**Critical:** For new incoming emails, use Odoo's native `message_new()` instead of manual record creation + `message_post()`.

```python
# CORRECT - uses Odoo's native flow
record = Model.message_new(msg_dict, custom_values=custom_values)

# WRONG - triggers unwanted notifications
record = Model.create(vals)
record.message_post(body=body, ...)  # Sends follower notifications!
```

**Why:** `message_new()` is what Odoo's standard mail gateway uses. It:
- Creates the record correctly
- Posts the initial message
- Triggers auto-replies (Helpdesk acknowledgment)
- Does NOT send duplicate notifications to sender

### Notification mailbox required for incoming sync
When enabling incoming sync on any mailbox, a Notification mailbox must exist. This handles:
- Emails from external authors (no Odoo user)
- System notifications triggered by incoming mail

## Odoo Settings Page Layout (res.config.settings)

### Two-column layout in settings - WHAT WORKS

For custom two-column layouts in Odoo 19 settings pages, **don't use** nested `<group>` elements inside `<setting>` - they don't render side-by-side.

**Working pattern** (Bootstrap grid + Odoo CSS classes):

```xml
<h2>Section Title</h2>
<div class="row mt-4 mb-4 o_settings_container">
    <!-- LEFT COLUMN -->
    <div class="col-12 col-lg-6 o_setting_box">
        <div class="o_setting_left_pane"/>
        <div class="o_setting_right_pane">
            <span class="o_form_label">Column Title</span>
            <div class="text-muted mb-3">Description</div>
            <div class="content-group">
                <div class="row mt-2">
                    <label class="col-lg-3 o_light_label" for="field_name">Label</label>
                    <field name="field_name"/>
                </div>
            </div>
        </div>
    </div>
    <!-- RIGHT COLUMN -->
    <div class="col-12 col-lg-6 o_setting_box">
        <div class="o_setting_left_pane"/>
        <div class="o_setting_right_pane">
            <span class="o_form_label">Column Title</span>
            <div class="content-group">
                <!-- content here -->
            </div>
        </div>
    </div>
</div>
```

### What does NOT work for two-column layouts

1. **Nested `<group>` inside `<setting>`** - groups stack vertically, not side-by-side
2. **`<group><group/><group/></group>` pattern** - works in forms, NOT in settings
3. **Bootstrap `row`/`col-6` inside `<setting>`** - gets overridden by settings CSS
4. **`<block>` with custom grid** - block has its own layout constraints

### Standard settings pattern (single items)

For standard label-on-left, content-on-right settings, use the normal pattern:

```xml
<block title="Section">
    <setting string="Label" help="Tooltip text">
        <field name="field_name"/>
    </setting>
</block>
```

## OWL Frontend (Odoo 17+)

Custom list view buttons require JavaScript. Pattern:

1. **Controller** (`static/src/js/my_list_controller.js`):
```javascript
/** @odoo-module */
import { ListController } from "@web/views/list/list_controller";
import { listView } from "@web/views/list/list_view";
import { registry } from "@web/core/registry";

export class MyListController extends ListController {
    onCustomAction() {
        this.actionService.doAction({ type: "ir.actions.act_url", url: "/my/url" });
    }
}

export const myListView = { ...listView, Controller: MyListController, buttonTemplate: "my_module.MyListButtons" };
registry.category("views").add("my_list", myListView);
```

2. **Template** (`static/src/xml/my_list_view.xml`):
```xml
<t t-name="my_module.MyListButtons">
    <button class="btn btn-secondary" t-on-click="() => this.onCustomAction()">Button</button>
</t>
```

3. **Manifest**: Add to `web.assets_backend`
4. **View**: Use `js_class="my_list"`

## Context Management

After every `/compact`, update the **Lessons Learned** section below with new insights from the session (gotchas, errors, solutions). This ensures continuity across context compactions.

## Lessons Learned

### Docker
- **`data_dir` must match volume mount**: `data_dir = /var/lib/odoo` in odoo.conf must match `odoo-data:/var/lib/odoo` in docker-compose.yml. Mismatch = filestore lost on restart.
- **Stale asset attachments after volume reset**: If assets 404, run: `docker-compose exec -T db psql -U odoo -d test_db -c "DELETE FROM ir_attachment WHERE url LIKE '/web/assets/%';"` then restart. Odoo regenerates them.
- **`--dev=reload` crashes on macOS Docker**: watchdog inotify doesn't work reliably in Docker on macOS. Use `dev_mode = xml` only in odoo.conf.
- **Don't duplicate dev_mode**: Set `dev_mode` in odoo.conf only, not also `--dev=all` in docker-compose command.
- **`.dockerignore` at build context root**: Build context is `../../` (GitHub parent). Without `.dockerignore`, all repos are sent to Docker daemon. Add `*` + `!odoo-enterprise/`.
- **`addons_path` needs 3 entries**: `/opt/odoo/odoo-enterprise` (enterprise addons like account), `/opt/odoo/odoo-enterprise/odoo/addons` (core addons like base, web), `/mnt/extra-addons` (custom addons).
- **Never `docker-compose down -v`**: The `-v` flag deletes volumes including filestore. Use `docker-compose down` without `-v`.

### OAuth / Graph API
- **OAuth tokens only contain requested scopes**: Adding permissions in Azure Portal is not enough - the scopes must also be listed in the authorization URL in `providers/microsoft/graph_client.py`. Users need to re-authenticate after scope changes.
- **Google only hands back a refresh token with `access_type=offline` + `prompt=consent`**: without both, a re-authorizing user gets an access token only and the account silently stops working an hour later. Google also omits the refresh token on *refresh*, so never overwrite a stored one with an empty value.

### Settings UI
- **A settings page that shows every provider at once reads as broken in every direction.** Azure asks for a tenant, Google does not; side by side, each form looks like it is missing fields the other has. Ask "where is your mail?" first and show one provider's steps.
- **Odoo 19's default selection widget is already a searchable dropdown.** `web.SelectionField` renders a `SelectMenu` with `searchable="!isBottomSheet"`, so a plain `<field name="x"/>` on a Selection gives the search box for free - no custom OWL widget, no `widget=` attribute.
- **`res.config.settings.get_values()` runs *after* `default_get()` read the config parameters**, and the base implementation returns `{}`. So `if not res.get(field)` is always true there and silently clobbers a stored value; check the `ir.config_parameter` itself before filling a gap.

### Migrations
- **A migration folder below the installed version never runs.** Odoo only runs scripts whose version is *higher* than what is installed. A long-lived branch that pins `migrations/19.0.1.2.0/` while mainline moves to 19.0.2.0.1 ships a migration that is dead on arrival - and silently so, because nothing errors. When merging a branch forward, re-check the migration folder name against the new manifest version, not the old one.
- **Copy Fernet ciphertext, never decrypt and re-encrypt.** Same key, same DB: moving the encrypted string is lossless and cannot fail halfway. A decrypt/re-encrypt cycle produces garbage you only discover at the next send.

### IMAP / SMTP
- **A provider without OAuth is the test of whether the contract is really provider-neutral.** Every place that asked "does this account have a refresh token" was an OAuth assumption wearing a neutral name. `account_is_connected()` moved that answer into the client, and `mail.mail` stopped gating sends on an access token an IMAP account will never have.
- **An IMAP UID is not a message id.** It is folder-scoped and dies with `UIDVALIDITY`. Store `folder:uidvalidity:uid` and refuse the fetch when the server renumbered — a bare UID silently fetches a *different* message.
- **`SEARCH SINCE` is date-granular and evaluated in the server's timezone.** Ask a day wide and narrow it in Python; the processor dedups on Message-ID, so overlap costs nothing and a too-narrow window loses mail.
- **Take the OLDEST N of a backlog, not the newest.** The cursor advances to the last message of the batch, so fetching the newest N steps over everything older and never comes back for it.
- **SMTP files nothing in Sent.** Graph and Gmail do it for you; here the client APPENDs the copy itself, best-effort — the mail is already delivered, so a failed copy must not be reported as a failed send.
- **`imaplib.Internaldate2tuple` returns *local* time and `%b` is locale-dependent.** Parse INTERNALDATE with an explicit month table into naive UTC, which is what the sync cursor compares against.

### Provider neutrality
- **A provider-aware *dropdown* is not a provider-aware *feature*.** Phase 3 made the mailbox owner domain accept Google-connected users, which made Gmail look supported. Every behavioural check underneath still asked `x_microsoft_oauth_connected`, so a Gmail mailbox listed a valid owner and then reported `error`, never synced, and fell back to the notification mailbox on send. Grep the *checks*, not just the fields.
- **"Is this user connected" is the wrong question. "Does this mailbox have usable credentials" is the right one.** The first hardcodes a provider; the second is `mailbox._has_working_credentials()`, which asks the client. It is also the only phrasing that works for a Gmail shared mailbox, which has credentials but no owner at all.
- **A stored compute cannot depend on a searched relation.** `x_incoming_enabled` tracks `x_owner_user_id.x_pan_mail_account_ids.connected`, but a service account is found by address, so authorizing one later does not retrigger it. The read side documents the limit; the write side closes it — `pan.mail.account.create/write` recomputes the mailboxes holding that address.
- **A cron filtered on `x_owner_user_id != False` silently excluded every shared mailbox that has no owner** — which on Gmail and IMAP is all of them. Filter on usable credentials (`_has_working_credentials()`), which is the question the filter meant to ask.

### CI
- **A passing suite is not the same as a suite that ran.** `0 failed, 0 error(s) of 0 tests` is green. So is a run where nine tests skipped themselves because an optional module was absent. Both mean "we verified nothing" and both looked identical to "everything passed" until the assert step started reading the count and printing the skips.
- **What CI never runs is where the bugs live.** `migrations/` was excluded from ruff *and* never executed by CI, because CI only ever installed fresh. Two blind spots stacked on the one directory that only ever runs on a customer's database, unattended.
- **Test the provider you ship, not just the one you just wrote.** The Gmail client had 36 tests including the whole token lifecycle; the Graph client — 1100 lines, in production at every customer — had none for refresh, rotation or revocation. New code attracts tests; the code that already works quietly stops earning them.

### Fail-open configuration
- **An empty setting must not mean "no restriction" when the restriction is the safety.** `_is_internal_domain` returned False when no domain was configured, so the databases that never set it up were exactly the ones that filtered nothing. The bug is invisible from the code — it reads like a normal guard clause — and only shows up as confidential mail appearing in Odoo. Ask what an unanswered question resolves to.
- **Gate the configuration, not just the runtime.** Blocking the sync run tells you *after* someone tried; blocking the save tells you before. Both are needed: the constraint stops the mistake being made, the runtime check stops it being un-made later by emptying the list.
- **Don't infer a security setting from a field that means something else.** `mail.alias.domain` is Odoo's *inbound* alias domain, auto-created at install and not necessarily the company's sending domains. Reusing it made the filter look configured when it was not.

### Onboarding order
- **Check mainline before building a picker.** This branch grew its own `x_setup_provider` while `19.0` merged a `x_mail_provider` reading `PROVIDER_SELECTION` from the registry. Both stored the same config parameter, so the merge produced two pickers over one value — and mine hardcoded outlook/gmail and would have silently omitted the IMAP provider that landed in between. The registry-driven one won; the checklist steps that are *not* about the provider (domains, notification mailbox, users) were ported on top and read `x_provider_credentials_set` / `x_provider_connected` instead of asking per provider.
- **A module that takes over a channel must not take it over before it can serve it.** The install hook disabled every SMTP server, but Mail Pro cannot send until an app registration, an OAuth grant and a notification mailbox exist — and the admin needs to email their users to get the OAuth grants. Take over at the moment you can actually deliver (first mailbox created), not at install.
- **Cancelling mail during setup destroys the evidence and the mail.** Internal notifications in the not-configured-yet window are queued with a readable `failure_reason` instead; the mail queue delivers them once setup finishes. Cancel only what you would never be able to send.

### Merging long-lived branches
- **Two branches solving the same problem is a design decision, not a merge conflict.** `19.0` and `refactor/provider-abstraction` both built a provider abstraction. Git merged them into a codebase with *both*, which compiles and is wrong. Pick one contract deliberately, migrate the other onto it, and delete the loser - do not let `git merge`'s "keep both" default make the architectural choice.
- **Prefer the abstraction the mainline already proved.** `mail.provider.client` won because Microsoft was already fully migrated onto it with tests and green CI, and because it is one layer (the client *is* the implementation) rather than two. The refactor's `pan.mail.provider.*` adapters only renamed dict keys.
- **Losing a branch's abstraction is not losing its substance.** `pan.mail.account`, the token migration, the `pan_mail_fetcher` rename and the whole Gmail client all survived - they were ported onto the winning contract, not discarded with the adapter layer.
- **A rename plus an edit on the same file is the merge's real trap.** `microsoft_incoming_mail.py -> pan_mail_fetcher.py` on one side, savepoint isolation added on the other. Git resolves the rename but the content conflicts read as pure noise; take the edited side's content wholesale at the renamed path instead of hand-merging hunk by hunk.
- **Unify selection *values* across models that name the same thing.** The mailbox shipped `x_provider='outlook'` while the branch used `provider='microsoft'`. Both now read `PROVIDER_SELECTION` from the registry, so an account and its mailbox cannot disagree - and no data migration was needed, because the shipped value won.

## Documentation

- [README.md](README.md) - Setup instructions for users
- [ARCHITECTURE.md](ARCHITECTURE.md) - Technical details for developers
