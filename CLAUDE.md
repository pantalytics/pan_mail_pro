# Claude Code Context

How to *work* on this module: environments, commands, CI, and the Odoo-specific
traps. The **design** — models, seams, and why each decision is what it is —
lives in [ARCHITECTURE.md](ARCHITECTURE.md). Read that first when the question
is "how does this work"; read this one when the question is "how do I change
it".

## Module Overview

**pan_mail_pro** - Microsoft 365, Google Workspace and IMAP/SMTP email
integration for Odoo 19.0, Community or Enterprise.

Send and receive emails via the Microsoft Graph API, the Gmail API (both OAuth
2.0 delegated) or plain IMAP/SMTP with a server, login and password.

The vocabulary — chatter vs. email, incoming vs. outgoing, mailbox, account,
provider — is fixed in ARCHITECTURE.md §1 and every name in the code follows
it. The module rename from `pan_outlook_pro` landed in 19.0.4.0.0; the
provider-neutral rename of models, fields, xml ids and config parameters in
19.0.6.0.0 (`migrations/19.0.6.0.0/` is the map).

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
| `models/mail_mail.py` | Outgoing override. `_resolve_route()` decides the sender once, and raises `RoutingError` rather than picking a different one |
| `models/mail_message.py` | The communication lens fields |
| `models/mail_thread.py` | The boundary: a message the sync imported notifies nobody |
| `models/mail_compose_message.py` | Composer "Send From" dropdown + setup warning |
| `models/mail_alias.py` | Cleaner alias display (name only, no domain) |
| `models/pan_mail_mailbox.py` | Mailbox configuration + routing rules |
| `models/pan_mail_account.py` | Credentials for one address on one provider |
| `models/providers/microsoft/graph_client.py` | Microsoft 365 implementation of the contract |
| `models/providers/google/gmail_client.py` | Gmail implementation of the contract |
| `models/providers/imap_smtp/imap_client.py` | IMAP/SMTP implementation of the contract |
| `models/providers/mime_utils.py` | Outgoing MIME, shared by the two MIME senders |
| `models/pan_mail_fetcher.py` | Incoming email sync (uses `message_new()`) |
| `models/pan_mail_matcher.py` | Thread matching: which Odoo record does this mail belong to |
| `models/pan_mail_thread_index.py` | The two indexes the matcher reads (Message-IDs, thread→record) |
| `models/pan_mail_routing_log.py` | Where each incoming mail landed and why (+ review queue) |
| `models/pan_mail_domain.py` | Internal domain list + the fail-closed gate on incoming sync |
| `models/pan_mail_provider.py` | The application registration of the provider this database runs on. One row, no toggle |
| `models/pan_mail_setup.py` | The three mandatory setup steps and the phase (`setup` / `syncing`) they add up to |
| `models/pan_mail_license.py` | Link to a Pantalytics account: Connect, the signed entitlement, the daily heartbeat, and `sync_allowed()`: incoming sync and new accounts need a connected instance |
| `models/neutralization.py` | Is this database a copy? Asked by `decrypt_value` (the hard gate) and by the callers that can say why |
| `models/res_partner.py` | Contact block list field |
| `models/res_users.py` | A user's accounts, their connected flag, connect / disconnect, and whether to nudge them |
| `models/res_config_settings.py` | The Settings page: the three checklist steps and the users block |
| `models/encryption_utils.py` | Fernet at rest for every credential, and where the key comes from |
| `models/ir_http.py` | Three session flags: does this user still have to connect a mailbox, may they open the Inbox at all, and may the Inbox report how it is used |
| `static/src/js/improve.js` | Help improve Mail Pro, the browser side: loads posthog-js from its own lazy bundle only when the session says so, five named events, a wireframe recording of the Inbox and nothing else, sent to our proxy, never to PostHog |
| `static/lib/posthog/` | posthog-js, vendored (MIT), pinned so the masking check in `tools/ui_check.py` proves the version that ships |
| `tests/test_improve.py` | What the session carries once the workspace said yes: a host, a token, a pseudonym, nothing that names anyone |
| `controllers/main.py` | One OAuth callback implementation, two provider routes |
| `models/pan_mail_coverage.py` | Link-coverage measurement: the screen, and `counts_since()`, whose last 24 hours ride the heartbeat |
| `models/pan_mail_conversation.py` | The read side of the Inbox: the RPC methods behind the screen, no table, no sudo for an answer. `conversation_messages()` is the one the chevron in the conversation list unfolds, and it returns three fields per mail rather than a body |
| `static/src/js/conversation_view/conversation_view.js` | The Inbox itself: four panes (**mailbox list, conversation list, conversation, Odoo record** -- the names are fixed in ARCHITECTURE.md §1), one client action, the tab strip (Mail / Mail + notes / Files / Activities) that replaced the record pane's chatter, the Followers button at its end that opens the chatter's own follower list, and the chevron in the conversation list that unfolds a conversation into its own mails and opens the pane on the one you pick |
| `static/src/js/chatter_door.js` | Door 1: **Open in mail** in Odoo's own chatter, on a record that carries an emailed message. Counts the record's threads, then opens the Inbox on the conversation or on the record's list |
| `static/src/js/conversation_view/use_panes.js` | How wide each pane is, which ones are folded away (the mailbox list from a round button in the top bar left of New Email, the conversation list and the Odoo record from a round button on their own divider), and whether the record has the screen to itself. Dragged, keyboard-resizable, stored in the browser -- except the zoom, which is a reading mode and not a preference, and the window's shape: below 1400px the conversation and the Odoo record take turns in one column, swapped from the record's divider button; below 768px every pane takes turns and the mailbox list is a drawer. A folded pane stays in the DOM at no width so the fold animates |
| `static/src/js/conversation_view/use_composer.js` | The reply, in the conversation pane instead of a dialog: Odoo's own composer form, the inline view it needs, and the Send that saves it and calls `action_send_mail` |
| `tests/test_conversation_api.py` | What the Inbox may show, and to whom |
| `tests/test_read_state.py` | Who decides whether a mail is read: the provider, mirrored onto `mail.message`, and the one way Odoo's own bell is cleared |
| `tests/test_provider_contract.py` | Guards the contract seam itself |
| `tests/test_connect_banner.py` | Who is asked to connect a mailbox, and who is left alone |
| `tests/test_oauth_routes.py` | Every route this module opens, who may call it, and what the OAuth callback stores when it works and when it refuses |
| `tests/test_incoming_mail.py` | Unit tests for incoming mail processor |
| `tests/test_mail_matcher.py` | Unit tests for the matching ladder |
| `tests/test_imap_provider.py` | IMAP/SMTP client (fake imaplib/smtplib, no sockets) |
| `tests/test_imap_live.py` | The same client against a real IMAP/SMTP server (GreenMail in a container). Skips itself when `PAN_TEST_IMAP_HOST` is unset |

## Provider Architecture

The design — the contract, the model map, thread matching, outgoing threading,
the routing log and the coverage report — lives in
**[ARCHITECTURE.md](ARCHITECTURE.md)**. It is not repeated here; two copies of a
design drift, and the one in the file you did not open is the one you believe.

What matters while writing code:

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
send-result shapes documented in `models/mail_provider_client.py`. The
boundary is enforced by a grep in CI, so breaking it fails the build rather
than review. A second grep keeps `anthropic` out of the module entirely:
19.0.7.0.0 removed the AI seam, and the cheapest way to keep it removed is a
check that fails the moment the import comes back.

Provider implementations live under `models/providers/<vendor>/`, so the
boundary is a directory you can grep rather than a convention you have to
remember. There is exactly **one** layer: the client *is* the contract
implementation.

### Adding a provider

1. Add the code to `PROVIDER_CLIENTS` and `PROVIDER_SELECTION` in `mail_provider_client.py`
2. Create `models/providers/<vendor>/<name>_client.py` with
   `_inherit = 'mail.provider.client'` implementing the contract
3. Declare its capabilities (`supports_shared_mailbox`, `supported_mailbox_types`, `uses_oauth`)
4. Add an ACL row in `security/ir.model.access.csv`
5. If it does not use OAuth, override `account_is_connected()` — the default
   answer is "has a refresh token", which no password provider ever will
6. Document it in the capability table in ARCHITECTURE.md §1

The same code is used by `pan.mail.mailbox.provider` **and**
`pan.mail.account.provider` — both read `PROVIDER_SELECTION`, so an account and
the mailbox it serves can never disagree about the provider's name.

No call site outside the client changes. `tests/test_provider_contract.py` covers
the seam; a new provider must satisfy the same assertions.

### Adding a gate (a reason to refuse incoming mail)

1. Write `_gate_<name>(self, ctx)` on `pan_mail_fetcher`, returning `None` to
   pass or `Skip(reason, detail, record=...)` to refuse
2. Add its name to `_gate_rules()`, in the position its assumptions require
3. Say in the docstring what it refuses **and why it sits where it sits** —
   order is the contract, and a gate that moves silently changes what the
   gates after it may assume
4. Decide `record`: does a person want this one back? Only the sync-mode gate
   does today. A gate that refuses on the contact's own objection must leave
   no trace at all
5. Document it in the ladder table in ARCHITECTURE.md §3

Nowhere else. A `return False` inside `_process_message` is the thing this
ladder exists to stop; the last one hid a filter that guarded one folder and
not the other for months.

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
└── odoo-customer-example/
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
| odoo-customer-example | `../addons:/mnt/extra-addons` | All addons via submodules |

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
| Tracks | branch `19.0`: a push pulls the code and restarts Odoo. It has never run `-u` (issue #134), so a version bump lands via Apps → Mail Pro → Upgrade |
| Login | `admin` / secret `CLOUDPEPPER_MAILPRO_DEV_ADMIN_PASSWORD`, project `prod` in Bitwarden Secrets Manager |

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
  `alias_id` link to `helpdesk.team`, and ticket creation through `message_new()`.
  `tests/test_incoming_mail.py` skips that class on a missing `helpdesk.team`, here
  as in CI. Test it locally against the Enterprise source — the third-party
  `helpdesk_community` addons are no substitute, because this code names
  `helpdesk.team` and `helpdesk.ticket` directly and those use their own models.

**A push restarts; it does not upgrade.** Cloudpepper pulls the code and restarts
Odoo on every push, and Python, view and asset changes land on that restart. New
models, menus, fields and migrations need `-u pan_mail_pro`, which the instance
has never run on its own even with `auto_upgrade` on (issue #134): after a merge
that bumps the manifest version, open Apps → Mail Pro → Upgrade, then check
`ir_module_module` says the new version. Odoo only executes migration scripts
when that version is *higher* than what it records, so the bump the Odoo 19
checklist asks for is what makes the upgrade do anything.

**Odoo core and addons are two separate update tracks, and neither migrates the
database on its own.** Core first, addons second, `-u` third -- the order and the
white screens it prevents are in
[docs/cloudpepper-deploy.md](docs/cloudpepper-deploy.md).

## CI/CD (GitHub Actions)

Three workflows in `.github/workflows/`:

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| `ci.yml` | every push + PR | lint (ruff), XML well-formedness, Odoo 19 checklist greps, manifest data-file check, version-bump check (PRs only), full test suite in a real Odoo (fresh install **and** upgrade from the last release), and the UI checks in a real browser |
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

**A real mail server, not only a fake.** `tools/ci_odoo.sh` also starts a
GreenMail container on the same Docker network and passes its hostname to Odoo
as `PAN_TEST_IMAP_HOST`. `tests/test_imap_live.py` then sends a message over
SMTP and reads it back over IMAP, which is the one thing a faked `imaplib`
cannot prove: that a server accepts what the client says. Without the variable
the class skips itself, so a bare `--test-enable` outside this script stays
green. GreenMail forwards nothing to the outside world, so "does the mail reach
a real recipient" is still a question only a real hoster answers.

Both test jobs end in `tools/ci_assert_tests.sh`, which fails the build when
Odoo reports no summary, any failure, **or zero tests** — the last one because
`0 failed, 0 error(s) of 0 tests` is otherwise a green build that verified
nothing.

The series is **derived from `__manifest__.py`**, not hardcoded: `19.0.1.3.0`
→ `odoo:19.0`. An addon repo carries one Odoo version per branch, so a future
`18.0` branch tests against Odoo 18 without editing the workflow.

Run CI yourself — this is not an approximation, the workflow calls the same
scripts:

```bash
tools/ci.sh lint                  # ruff, XML, the Odoo 19 checklist, boundaries (seconds)
tools/ci.sh test                  # fresh install + full suite in a real Odoo (~4 min)
tools/ci.sh upgrade               # install last release, upgrade, run the suite
tools/ci.sh ui                    # the browser checks against a seeded instance
tools/ci.sh                       # all four, in CI's order

BASE_REF=origin/19.0 tools/ci_lint.sh   # + the diff-shape and version-bump checks
```

Docker and Docker Hub access are the only requirements. `tools/ci_odoo.sh`
starts its own Postgres container, so there is nothing to set up and nothing
left running afterwards. No Enterprise source, no Azure credentials, no local
Odoo — which is why it also works unchanged inside a Claude Code cloud session.

Every check lives in `tools/`, never inline in the YAML. A check that only
exists in a workflow file is a check nobody can run before pushing.

| Script | What it is |
|--------|------------|
| `tools/ci.sh` | Entry point. `lint`, `test`, `upgrade` or all three |
| `tools/ci_lint.sh` | Every static check the lint job runs, including that every `tests/test_*.py` is imported by `tests/__init__.py` — a file that is not on that list never runs, and nothing else can see that |
| `tools/ci_version_bump.sh` | The manifest version bump, against a base ref |
| `tools/ci_odoo.sh` | Postgres + Odoo in Docker; `--mode=fresh` or `--mode=upgrade` |
| `tools/ci_assert_tests.sh` | Reads the Odoo summary: no failures, and not zero tests |
| `tools/ci_rename_rehearsal.sh` | The pre-rename customer path: install `pan_outlook_pro` at an old tag (or restore a customer backup with `BASE_DUMP=`), run the rename SQL, upgrade to HEAD across every migration. Not in CI — run it before a rollout |
| `tools/ci_ui.sh` | The UI job: boots that instance, runs `ui_check.py` against it, keeps the screenshots |
| `tools/ui_check.py` | The browser assertions — checklist width, one dot per step, no selection codes on screen, every menu opens, and the Inbox: four filled panes, everything clickable a real button, Reply opening the composer in the pane and not in a dialog, one open message in a collapsed thread, the record pane stepping aside at 1280px and sliding in from the strip on its divider, the four tabs opening without a traceback, no chatter left in the record pane, the dividers dragging, folding and surviving a reload, the record taking the whole screen and giving it back, the phone showing one pane at a time with a way back from each, and linking a conversation from the suggestion so the correction actually reaches the database |
| `tools/ui_preview.sh` | A running Odoo with the module installed and seeded, at http://localhost:8069. Not a check — the thing you look at |
| `tools/ui_shot.py` | Screenshots a settings tab of that instance with Playwright |
| `tools/docs_to_knowledge.py` | Renders `docs/` into the knowledge-base article bodies. Not a check: the docs live in two places and this is what keeps the published copy honest |

**Fresh install vs. upgraded database.** The `test` job installs fresh; the
`upgrade` job installs the newest `v<series>.*` tag that is not HEAD and then
runs `-u pan_mail_pro --test-enable` on that database. So the suite runs twice,
against both shapes, and the scripts in `migrations/` finally execute somewhere
other than a customer's database. Tests that touch columns of unstored fields
must still create those columns themselves — see
`tests/test_account_migration.py::_ensure_legacy_columns`.

The `pan_outlook_pro` → `pan_mail_pro` rename is *not* covered by that job: it
happens outside Odoo (`tools/rename_to_mail_pro.sql`), before the registry
loads, so no module upgrade can drive it. It stays a manual runbook —
`tools/ci_rename_rehearsal.sh` walks it end to end (old tag → rename SQL →
upgrade to HEAD → full suite), which is the only thing that exercises more than
one migration folder at a time. Run it before a rollout; it is not in CI
because it takes a second Odoo install.

**The upgrade job hops one version.** A customer sitting on 19.0.1.x crosses
eight migration folders in a single `-u`, and nothing in CI does that. The
rehearsal does, but from a *fresh* baseline, so every data-moving migration
logs "0 rows". `BASE_DUMP=/path/to/backup tools/ci_rename_rehearsal.sh` closes
that last gap: it restores a customer backup into the throwaway database
(neutralized, so it cannot mail anyone), takes the same rename-and-upgrade
path, and prints real row counts and a masked parameter report. Customer data
means it can never run in CI; it is part of the rollout runbook
(`docs/migration-mail-pro.md`).

## Looking at the UI

The suite asserts that a view *renders*. Nobody was asserting that it *reads*:
the setup checklist ran the full width of the window, so on a wide monitor the
arrow sat a screen away from the step it belonged to, and the provider line
showed `outlook` where its own form says "Microsoft 365". Both were plainly
visible and invisible to 456 passing tests.

`tools/ci_ui.sh` is now a CI job. It boots a seeded Odoo, walks every menu the
module declares and asserts the shapes that broke before — the checklist's width at a
2000px window, one status dot per step, no selection code where a label
belongs, the version and the licence line in About, and that every menu entry
opens without a traceback. The screenshots it takes are uploaded as the
`ui-screenshots` artifact, so a layout change is reviewable from the PR.

```bash
pip install playwright           # once — the browser is already on the image
tools/ci.sh ui                   # the CI job, locally

tools/ui_preview.sh              # or boot it yourself: localhost:8069, admin/admin
tools/ui_shot.py before.png --width=2000
# ... edit a view or the scss ...
tools/ui_preview.sh --update     # re-apply views and assets
tools/ui_shot.py after.png --width=2000
tools/ui_preview.sh --stop
```

Same Docker-only requirement as the rest of `tools/ci.sh`, so it works in a
cloud session where there is no browser and no laptop. Take screenshots at
2000px as well as 1440: a layout bug that only a wide monitor shows is still a
layout bug, and that is the width the complaint arrives from.

## Working from Claude Code (mobile, web, cloud sessions)

`19.0` is protected: no direct pushes, PR + green CI required. The full loop
without a desktop:

```bash
git checkout -b feature/<name>
# ... changes ...
tools/ci.sh                     # the same checks CI will run, before pushing
git commit -am "..."            # bump __manifest__.py version if code changed
git push -u origin HEAD
```

Then open the PR and let GitHub merge it when CI turns green. Two ways,
depending on where the session runs:

- **Terminal with `gh`:** `gh pr create --fill --base 19.0` then
  `gh pr merge --auto --squash --delete-branch`. Check back with
  `gh pr checks` or `gh run watch`.
- **Cloud session (claude.ai/code):** there is no `gh` CLI. Use the GitHub MCP
  tools instead — `create_pull_request`, then `enable_pr_auto_merge`, and
  `pull_request_read` or `actions_list` for status.

`--auto` / auto-merge is the part that makes this work unattended: GitHub
merges the moment CI is green, so there is nothing to come back to.

**Running the suite before you push is the whole point in a cloud session.** A
push-and-wait loop costs four minutes per round trip and burns a CI run per
typo; `tools/ci.sh` gives the same verdict locally in the same container.

## Conventions

- `x_` prefix only on fields added to Odoo's own models (Odoo.sh requirement); a
  `pan.mail.*` model has plain field names
- Log tags name the flow or the vendor: `[Outgoing Mail]`, `[Incoming Mail]`,
  `[Mail Matcher]`, `[OAuth]`, `[License]`, and `[Graph API]` / `[Gmail API]` / `[IMAP]` /
  `[SMTP]` inside the matching client only
- Use `invisible` instead of `attrs` in views (Odoo 19)
- Stored computed fields need `@api.depends` decorator

## Common Tasks

### Adding a new Graph API method
1. Add it to `models/providers/microsoft/graph_client.py` — nowhere else may
   build a Graph URL or read a Graph payload
2. Use `get_valid_token(account)` for authentication
3. Handle errors with `_extract_graph_error()`
4. Return the normalized shape from `mail_provider_client.py`, not Graph's

### Debugging email issues
1. Check Odoo logs for `[Outgoing Mail]` and `[Incoming Mail]` tags
2. Verify credentials: `user.x_pan_mail_connected`, or ask the mailbox itself
   with `mailbox._has_working_credentials()`
3. Check mailbox state: should be 'active'
4. A mail that did not go out carries its own reason in `failure_reason`

## Traps that cost a release

Design rationale is in [ARCHITECTURE.md](ARCHITECTURE.md). These are the two
things people get wrong *while typing*, so they are repeated here on purpose.

**Use `message_new()` for incoming mail, never `create()` + `message_post()`.**
The manual version sends follower notifications, so the sender receives a copy
of their own email back. See ARCHITECTURE.md §9.3.

```python
# CORRECT — uses Odoo's native flow
record = Model.message_new(msg_dict, custom_values=custom_values)

# WRONG — triggers unwanted notifications
record = Model.create(vals)
record.message_post(body=body, ...)  # Sends follower notifications!
```

**Pass `date=` on every `message_post()` in the incoming path.** Odoo defaults it
to `now()`, which dates a historical import to the day it ran and destroys the
timeline. The normalized message carries the provider's own date.

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
- **A stored compute cannot depend on a searched relation.** `x_incoming_enabled` tracked `x_owner_user_id.x_pan_mail_account_ids.connected`, but a service account is found by address, so authorizing one later never retriggered it. It was patched from the write side (`pan.mail.account.create/write` recomputing the mailboxes holding that address) and then deleted in 19.0.5.0.0: the cron asks `_has_working_credentials()` at the moment it needs the answer, which is always right and needs no machinery at all. **A cache whose invalidation you have to hand-write is usually not worth having.**
- **A cron filtered on `x_owner_user_id != False` silently excluded every shared mailbox that has no owner** — which on Gmail and IMAP is all of them. Filter on usable credentials (`_has_working_credentials()`), which is the question the filter meant to ask.

### CI
- **A passing suite is not the same as a suite that ran.** `0 failed, 0 error(s) of 0 tests` is green. So is a run where nine tests skipped themselves because an optional module was absent. Both mean "we verified nothing" and both looked identical to "everything passed" until the assert step started reading the count and printing the skips.
- **What CI never runs is where the bugs live.** `migrations/` was excluded from ruff *and* never executed by CI, because CI only ever installed fresh. Two blind spots stacked on the one directory that only ever runs on a customer's database, unattended.
- **Test the provider you ship, not just the one you just wrote.** The Gmail client had 36 tests including the whole token lifecycle; the Graph client — 1100 lines, in production at every customer — had none for refresh, rotation or revocation. New code attracts tests; the code that already works quietly stops earning them.

### Testing against a real server
- **A fake proves the call, not the protocol.** `test_imap_provider.py` fakes
  imaplib and smtplib, which proves the client makes the right calls and cannot
  prove a server accepts them. GreenMail caught three things the fake never
  would: the login is the local part rather than the address, the server
  advertises no `\Sent` special-use flag so the fallback is what actually runs,
  and `Sent` does not exist until something creates it.
- **A test that polls beats a test that sleeps.** SMTP delivery is asynchronous
  on every server. A bare fetch after the send is the classic test that passes
  on a laptop and fails on a loaded runner.
- **Skip on a missing environment, do not fail.** The live class is gated on
  `PAN_TEST_IMAP_HOST`, so it runs where the container exists and is invisible
  where it does not — and `tools/ci_assert_tests.sh` still prints the skip, so
  it cannot go quiet unnoticed.

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

### Removing a feature (19.0.7.0.0)
- **A well-shaped seam with no caller is still dead code.** The AI seam was
  built right: a contract, a registry, a null backend that is a real
  implementation, a CI grep on the vendor import. It had exactly one caller,
  the triage queue's classification cron. Removing the queue removed the
  feature; the seam just took a release to notice. Count the callers before
  admiring the shape.
- **A queue nobody works is a copy of the data with none of the protection.**
  `pan.mail.item` held somebody's correspondence outside every document ACL, so
  it needed a rule of its own and could only ever be shown to mailbox managers.
  The refusal it existed to make reversible is reversible anyway, by widening
  the mailbox's sync mode, which re-reads the mail from the provider.
- **An app tile is a promise about how often a screen is opened.** The module
  had a "Communication" application on the home screen holding one read-only
  lens and a queue. Both are diagnostics. They are back under Settings →
  Technical → Email, next to the rest of the module.
- **Keep the grep after you delete the thing it guarded.** `anthropic` is now
  banned from the whole module rather than confined to `models/ai/`. A removal
  that leaves no check behind is a removal that comes back.

### Triage and correcting a match (19.0.12.0.0)
- **A `mail.message` with no `model` is visible to its author and almost nobody
  else.** Odoo's `mail.message._search` filters every row down to "you wrote
  it, you are a recipient, or you can read its document", and a message with
  no document has none of those for anyone else — not even an administrator,
  because the check is a rule the ORM applies rather than an ACL `sudo` would
  lift. The Inbox's "Linked to nothing" folder is therefore empty for everybody
  but the sender, which is invisible from the code and from every Python test
  that reads as the author. The real fallback state is `model = 'res.partner'`,
  the "On a contact only" folder, and that is the one triage works from.
- **Ask what a rule is worth before making it cheap.** `record_reference` earns
  its place above the guesses by *refusing*: the moment a subject names two
  documents, every hit drops below the routing threshold. A rule that always
  answers is a guess wearing a lookup's confidence.
- **A suggestion belongs where the mail is read, not in the audit table.**
  Settings → Technical is where you go to ask why; the screen where mail is
  read is where it gets fixed. The queue removed in 19.0.7.0.0 was the same
  mistake in another shape.
- **The correction is the feature, not the model.** Linking a conversation by
  hand writes a `pan.mail.thread.link`, so the rest of that conversation
  matches at rule 3 from then on — exactly, for free. That is the only part of
  triage that compounds, and it needs no AI at all.
- **"Filed on" was the one place the vocabulary drifted.** The mailbox list already
  said "Linked to nothing" and the report is called link coverage; the chip
  row said "Filed on" and the method was `refile`. One idea, two words, and
  the technical one had reached the screen. It is **linked** now, in the copy
  and in the code.
- **`--` is illegal inside an XML comment**, and Odoo's own loader will not
  tell you which file: `tools/ci_lint.sh`'s XML check does, in a second.

### All mailboxes (19.0.15.5.0)

- **The unified inbox was a row and a label over a query that already
  existed.** `_base_domain(mailbox_id=None)`, `folder_counts` keyed on mailbox
  `0`, `mailboxKey()` returning `0` for "no mailbox" -- all of it shipped in
  19.0.10 meaning "no mailbox is configured yet". With mailboxes it means all
  of them, and the same key carries the counts and the folds. A feature that
  needs no read path is a feature to check for before designing one.
- **"Every mailbox" also means every mail with no mailbox.** All mailboxes
  shows email-type messages that no `pan.mail.mailbox` owns -- chatter mail
  from before the module was installed, Odoo's own. That is what the domain
  says and it is honest, but it is why the row tag is empty on some rows and
  why the UI check asserts the two seeded threads by name rather than counting
  tags against rows.
- **The reply's sender was the silent half.** The composer defaulted
  `x_send_from_mailbox_id` to the folder's mailbox, which under All mailboxes
  is null: the reply then fell through to `_resolve_route()` and could answer
  from an address the customer never wrote to. Nothing errors, nothing logs.
  The row carries `mailbox_id` now and the reply reads the row, never the
  screen's state -- the mailbox of the *conversation*, not of the folder.
- **A UI check that seeds one mailbox cannot see a bug about two.** Every
  seeded mail was in one mailbox, so All mailboxes rendered identically to the
  mailbox under it and would have passed with the tag code deleted. The seed
  grew a thread in the second mailbox before the assertion was worth writing.

### Which pane takes the slack (19.0.15.2.0)

- **The elastic pane is the one whose divider stops doing anything.** Capping
  the conversation at its prose measure and giving the record `flex-grow`
  looks right and breaks the drag: once the capped pane freezes, flexbox
  hands the remainder to the grower, so its size is `total - others - cap`
  whatever basis the drag stored. The number is still written, still restored,
  and nothing reads it. Invert instead -- the pane with a natural ceiling keeps
  the stored width, the pane that reads better wider becomes the leftover.
- **A divider is named after a pane, not after the width it sets.** The record's
  divider now sizes the conversation, so `dragged()` is that mapping and
  everything the splitter reads goes through it -- including `aria-valuemin` /
  `aria-valuemax`. Miss one and `PANES[divider]` is `undefined`: Owl says
  "Cannot read properties of undefined (reading 'min')" with a template line
  number and nothing else, and the whole screen is an error dialog.
- **A ceiling reserves a neighbour's floor, not its width, when that neighbour
  shrinks.** Counting the conversation's full stored width made the list's
  ceiling smaller than the list already was, so the first drag snapped it to
  its minimum before moving.

### Naming the panes (19.0.14.1.0)

- **One idea, two words, and the loaded one reaches the code.** Pane 3 was
  `thread` in the code and "the conversation pane" in every sentence about it,
  while "thread" already meant the matcher's key (`pan.mail.thread.index`, the
  References root, Odoo's own `Thread` store). The pane is `conversation` now
  and the screen's root class is `o_mailpro_inbox`, which is what freed the
  name. Same mistake as "Filed on" vs "linked", one release later.
- **Rename the root before the child that wants its name.** Doing it the other
  way round makes `.o_mailpro_conversation` mean two things for one sed, and
  every rule nested under the old root silently changes scope.
- **A pane name is four strings, not one.** The code key, the CSS class, the
  label and the prose. `tests/test_inbox_panes.py` reads all four and asserts
  they agree, because a vocabulary nobody can run lasts one release.
- **Name a pane after what is in it, not where it sits.** `rail` said "the
  narrow one on the left", which describes the layout and survives no
  redesign; `list` and `record` said too little to tell apart from the things
  they hold. They are `mailbox_list`, `conversation_list`, `conversation` and
  `odoo_record`, and each one now answers "what is in that pane" on its own.
- **A module inside Odoo still has to say Odoo.** This screen puts mail on
  documents, so a bare `record` reads as either side of that sentence. The
  pane is the **Odoo record**; `selectedRecord` and the record chips keep the
  short word, because there the document is the only thing it can mean.
- **The lint job's diff-shape checks only run with `BASE_REF`.** A bare
  `tools/ci.sh lint` passes while CI fails on "changes models/ but no test" --
  run `BASE_REF=origin/19.0 tools/ci_lint.sh` before pushing.

### Small screens (19.0.13.4.0)
- **Odoo compiles SCSS with libsass, and libsass reads `min()` / `max()` as
  Sass's own functions.** `width: min(80%, 20rem)` fails on the mixed units,
  and a stylesheet that fails to compile takes the whole `web.assets_backend`
  CSS bundle down with it: every UI assertion went red at once, including the
  setup checklist's, which the change never touched. Two properties (`width`
  + `max-width`) say the same thing. `pip install libsass` and
  `sass.compile(string=...)` on the file is the ten-second local check.

### Reusing Odoo's own menus (19.0.13.0.0)
- **A dropdown renders in the overlay container, not inside the screen that
  opened it.** Nest its SCSS under the screen's root (`.o_mailpro_inbox`) and not one
  rule applies -- the menu is a sibling of the whole web client by the time it is
  drawn. `menuClass` plus a top-level block is the way.
- **The filter menu is `Dropdown` + `CheckboxItem`**, the same two components
  `web.SearchBarMenu` builds Odoo's own filter menu from, down to
  `class="{ 'o_menu_item': true, selected: isActive }"` and
  `closingMode="'none'"`. A full `SearchModel` would not fit: the Inbox reads
  through `pan.mail.conversation`'s own RPC methods, not through a
  `search_read` over a table, so there is no domain for a facet to become.
- **A composer opened in a dialog does not close on Escape when it has a
  draft in it**, so a browser check that presses Escape leaves a modal over
  everything it asserts next. Click the dialog's own close button.
- **The module that defines a model is not the app that shows it.** The
  Linked-to chip resolved its tile from `_original_module`, so a contact wore
  `base`'s icon: a teal cube nobody recognises, and a browser check that only
  asserts "the image loaded" is happy with it. The tile the reader knows is
  the root menu's `web_icon` of the app that opens the model (Contacts for a
  contact, `sale_management` for a sale order, which `sale` itself never is).

### Mounting Odoo's own views inside your own screen (19.0.10.0.0)
- **Bootstrap's display utilities carry `!important`, and Odoo's own markup wears them.** The form renderer is `d-flex flex-nowrap` in its wide layout and the statusbar's button row is `d-flex`; a plain `display: block` / `display: none` from an addon loses both times. The form then renders a chatter with no record above it, and the record's "Convert to Opportunity" stays as the loudest button on a screen whose one job is replying. Both cost a round trip in the browser to find, because nothing errors.
- **A group on a menu is not an access rule, and in 19.0 an action cannot carry one either.** `ir.actions.actions` has no group field, so a client action opens by URL for anyone who knows it, and every `@api.model` method on the model behind it answers `call_kw` from any session. The check belongs in the methods.
- **`limit` arrives over RPC.** A read method that passes the caller's `limit` into a search hands anybody a way to materialise the table. Clamp it where it enters.
- **Odoo 19 renamed `groups_id` to `group_ids`** on the models that still have it, and refuses `default_res_id` on the composer in favour of `default_res_ids`. Both fail loudly, but only in the browser.
- **A form view renders its `<footer>` only inside a dialog.** `FormController` cuts every footer out of the arch, and `web.FormView` draws it under `t-if="env.inDialog"`, portalled into the modal's footer. Mount the composer in a pane and Send, the paperclip and the template selector are simply not there -- no error, no log line. A primary view moves the widgets into the body; the buttons belong to whatever closes, which is the pane.
- **Driving a form from outside it needs the controller, not the DOM.** `View` passes unknown props straight to the controller and `FormController.props` is a strict schema, so there is no prop to hang "give me your record" on. A `js_class` of our own whose controller writes itself into the env is the seam; the arch's own `js_class` wins over the `jsClass` prop, so swapping it is an inherited-view attribute, not a JS argument.
- **A thread index that is unique on (provider, mailbox, thread) maps a thread to one record per mailbox.** A conversation that reaches two records is the cross-mailbox case, and the key that may cross a mailbox is the References root, never the provider's own handle: a Graph `conversationId` means something else in another mailbox.

### Enterprise vs community (19.0.7.7.1)
- **A `components` dict is a snapshot, and the edition you do not run takes it first.** `patch(WebClient, {components: ...})` works on community and white-screens Enterprise: `WebClientEnterprise` spreads `WebClient.components` in its class body, `web_enterprise` is bundled before this module, so the patch lands after the copy and Owl cannot resolve the tag. Bind the class to an instance attribute in a patched `setup()` and use `t-component`, which is read at render time.
- **A white screen with an empty server log is a frontend resolution failure.** Nothing on the Python side can see it, and it is invisible to a suite that only asserts views render.
- **CI runs the community image, so every Enterprise-only path is untested by construction.** The cheapest cover is a static check on the shape that breaks (a component tag in a borrowed template), not a test that needs an edition CI does not have.

### Boundaries (19.0.6.3.0)
- **A passthrough is a decision nobody made.** The contract documented `headers`
  as a faithful copy of the message's headers, so all three clients handed over
  `bcc` from the Sent folder. Nothing read it, so nothing broke — an open door
  with nothing behind it yet. Graph was clean only because
  `internetMessageHeaders` was missing from one `$select`, which a "more
  complete sync" would have quietly reopened.
- **Allow-list in the contract, not a strip call per client.** A strip call
  protects the providers that exist; a list on the seam protects the ones
  nobody has written. `normalize_headers()` is one method, three call sites and
  one test that fails when a new provider forgets it.
- **A field that exists leaks eventually**, through an export, the API, a report
  or a template. So the value never enters, rather than entering and being
  hidden behind a group.
- **Pin what is correct by accident.** The send path has no BCC because
  `mail.mail` has no field for one. That is absence, not a decision, so it now
  has a test: a future "add BCC support" argues with a failing assertion
  instead of landing quietly.

### Nomenclature (19.0.6.0.0)
- **An orphaned xml id on a field drops the column.** `ir.model.data._process_end` unlinks every record whose xml id the module no longer declares, with the uninstall flag set — and `ir.model.fields.unlink` under that flag calls `_drop_column()`. Rename a field in Python alone and the upgrade creates a new field row, orphans the old xml id, and drops the *renamed* column if it still carries the old name. Rename `ir_model_fields.name` and the xml id in pre-migrate, so the ORM finds its field already in place.
- **Renaming in pre-migrate means every older migration runs after the rename.** A database jumping from 19.0.3 to 19.0.6 runs *all* pre-migrates, then loads, then *all* post-migrates — so 19.0.4.0.0's post-migrate met a column 19.0.6.0.0's pre-migrate had already renamed. Older scripts have to tolerate both names; check the ones that touch a renamed table before shipping the rename.
- **Rename the parameter that *is* the key with a fallback in code.** A deploy that runs the new code without the version bump (Cloudpepper auto-upgrade does exactly that) would generate a fresh encryption key and orphan every stored credential. `get_encryption_key` adopts the old parameter before minting a new one.
- **Every rename is metadata-only in PostgreSQL.** `RENAME COLUMN` and `ALTER INDEX ... RENAME` rewrite nothing; keeping the ORM's index names is what stops it rebuilding a partial index on `mail_message` under a lock.
- **Three copies of a wire id is two too many.** The provider's Message-ID lived on `mail.mail` (deleted after send), on `mail.message`, and in the ref index. Only the index is read first by every lookup; the others were backfilled into it and dropped.

### Simplification (19.0.5.0.0)
- **Five fields computed from one field are five things that can disagree with it.** The mailbox had `x_sync_mode` plus `x_incoming_sync`, `x_sync_unknown_contacts`, `x_sync_inbox`, `x_sync_sent` and `x_incoming_enabled` — one three-way choice wearing six hats, each with its own compute, inverse and depends. The mode alone says everything; the rest was UI convenience that outlived the UI it was built for.
- **Check what mainline did with a field before deleting it as dead.** `x_routing_smart` and `x_queue_unknown_contacts` both looked like the same thing: a boolean whose only behaviour was a `ValidationError` refusing to let it be switched on. Both were deleted on the first pass. In between, 19.0.4.0.0 gave `x_queue_unknown_contacts` a triage queue to feed and named `x_routing_smart` as the explicit interlock the AI seam may not open yet. A field with no behaviour today is not automatically a field with no decision behind it — read the code that documents it, not just the code that uses it. (19.0.7.0.0 deleted both again, this time with the features behind them.)
- **A compatibility shim outlives its callers silently.** The five `res.users.x_microsoft_*` token proxies existed so pre-account callers kept working. Every one of those callers had since been rewritten; nothing but tests read them. Nothing fails when a shim goes stale, so nothing tells you — grep the callers before assuming a shim is still load-bearing.
- **Two controllers doing the same thing drift in ways one cannot.** The Microsoft and Google OAuth callbacks were 90 near-identical lines each. Only Microsoft logged the connected identity; only Google preserved a missing refresh token. Neither difference was a decision.
- **Reconstructing "why did this fail" after the fact is a second implementation of the decision.** `_get_missing_mailbox_error()` re-walked the whole routing tree to explain a failure the router had already diagnosed, and the two could disagree. Resolve once, raise with the reason.
- **Raising and recording are mutually exclusive in one transaction.** `mail.mail.send()` wanted to both tell the sender and leave the reason on the mail; the raise rolls the write back. Odoo's `assertRaises` makes this visible in tests (it opens a savepoint), production makes it visible as a mail that is still `outgoing` after an error dialog. Pick which one the caller gets, and say where the other one comes from — here, the cron's `auto_commit` pass a minute later.
- **A knob nobody should turn is a way to break the product from the settings page.** The Microsoft auth/token URLs were config parameters. They are the same for every tenant, and a wrong value is unrecoverable from the UI. Constants.

## Documentation

| File | What it is for |
|------|----------------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | **The design.** Models, seams, flows, and why. Single source of truth |
| [docs/product-brief.md](docs/product-brief.md) | **The product, high over.** Which problem, whose problem, who it is not for, and what we refuse to build. No field names. Start here |
| [README.md](README.md) | Setup and usage for the person installing the module |
| [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md) | UI conventions. Read before adding a field to a settings or mailbox screen |
| [TESTPLAN.md](TESTPLAN.md) | Manual test plan for what CI cannot reach |
| [docs/cloudpepper-deploy.md](docs/cloudpepper-deploy.md) | Deploying on Cloudpepper: the two update tracks, the order, and the white screens |
| `docs/` | End-user documentation, per provider. The source. Customers read the copy in the Pantalytics knowledge base (`pantalytics.odoo.com/knowledge/article/116`); `tools/docs_to_knowledge.py` renders these pages into it, so refresh the article when you change one |
| `docs/plans/` | Designs that are agreed but not built, and the half-shipped ones. [mvp.md](docs/plans/mvp.md) is the status review of 2026-09-18: what ships, what the MVP still needs, in what order. [conversation-view.md](docs/plans/conversation-view.md) is the current one, with [how it gets built](docs/plans/conversation-view-build.md) beside it. A plan carries a status line naming which parts ship; what has shipped moves into ARCHITECTURE.md, and the file leaves `docs/plans/` when the last step lands |
| `docs/research/` | The evidence behind those designs: customer surveys, what the platform already does, [the market](docs/research/competition.md), and [how other products show a customer](docs/research/customer-360.md) |
| CLAUDE.md (this file) | Workflow: environments, commands, CI, Odoo traps |

The split is enforced, not just intended: CI fails if a model in `models/` is
not named in ARCHITECTURE.md. When you add a model, document it there — not
here, and not in both.
