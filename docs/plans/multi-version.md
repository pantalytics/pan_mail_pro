# Mail Pro on Odoo 18, 19 and 20

Status: **step 1 in progress**, 2026-11-03. 18 passes the whole suite from
`main` through the seam (no branch yet); 20 installs and runs the suite with
the items below still open. The checklist at the end is the state; update it in the same PR as
the work, never in chat.

## What is asked

Mail Pro installs and passes its suite on Odoo 18.0, 19.0 and 20.0, Community
and Enterprise, and keeps doing so release after release with a team of one.

## What was measured

Against `odoo/odoo` `18.0` at 55f9d29c and `20.0` at 006f5cc9 (both
2026-11-03), PostgreSQL 16, Python 3.13, this module at 19.0.33.0.4, in a
cloud session without Docker (`tools/odoo_local.sh` is that setup, written
down).

**One branch per series is forced, not chosen.** A `19.0.x` manifest on 18 or
20 logs *"The module pan_mail_pro has an incompatible version, setting
installable=False"* and the install ends with `0 tests`. So the manifest's
series has to match the server's, and a runtime `version_info` switch inside
one branch cannot get past the loader.

**Odoo 18, with the series swapped in the manifest.** The registry stops at
the first model: `module 'odoo.models' has no attribute 'Constraint'`. The
module uses `models.Constraint` seven times; 18 only knows
`_sql_constraints`. `res.users` has `groups_id` (19 and 20: `group_ids`),
which the module touches in **tests only**, 58 lines across 24 files, none in
`models/`. `default_res_ids` on the composer is already the 18 rule, not a
difference. All 32 files the Inbox imports from `@web/` and `@mail/` exist in
18 at the same path; whether they export the same names is the next
measurement.

**Odoo 20, same swap.** The install fails on
`security/pan_mail_pro_security.xml`: `KeyError: 'ir.rule'`. Odoo 20 has
neither `ir.rule` nor `ir.model.access`; both are one model, `ir.access`,
loaded from `security/ir.access.csv` with the columns
`id,name,model_id,group_id/id,operation,domain` and an operation like `crud`
or `r`. Our 17 ACL rows and 7 record rules have to become that one file.
`mail.message` visibility is no longer a rule either: it is a set of computed
fields (`res_access_read` and siblings) in 20's own `ir.access.csv`, which
means the "a message with no model is visible to its author and nobody else"
lesson (CLAUDE.md, 19.0.12.0.0) must be measured again on 20 before the Inbox
is trusted there. `_sql_constraints` is refused with a warning ("no longer
supported, please define models.Constraint"), so 19 and 20 share that form
and 18 is the odd one. 31 of the 32 Inbox imports exist; `@mail/chatter/
web_portal/chatter` moved to `@mail/chatter/web_portal_project/chatter`.

**The whole Python delta for 18, measured by running the suite** (same day,
`tools/odoo_local.sh --series=18.0`, from "registry does not load" to 1314
tests running). Five things in `compat.py`: `models.Constraint` (a
`__set_name__` that appends to `_sql_constraints` does it, so the seven model
files are untouched), `fields.Domain` (a list with `AND` and `OR`),
`_postprocess_sent_message`'s `success_emails`, `_partner_find_from_emails_single`
(18: `_mail_find_partner_from_emails`), `_get_notification_status` (18 has
none; the table is copied from 20). One view anchor (`page_security` does
not exist on 18; the notebook does). Two behaviours, not names: 18 stores a
missing `res_id` as `0` and matches `= False` as `IS NULL` only, so every
"linked to nothing" domain now says `in (False, 0)`; and 18 enforces the
owner-only mailbox rule on the read inside `_is_sendable_by`, where 19 was
answering from the cache, so the boundary reads as the system and returns a
boolean. On the test side: `group_ids` (58 lines), `enter_registry_test_mode`,
`_commit_progress` and `_disable_send` (19-only, patched with `create=True`
or by the 18 switch), `_connect__` (18: `connect`), a `mail.message.create`
of a `user_notification` with a `res_id` (18 looks the reply-to up on an
empty recordset: the fixture hands `reply_to` in), and demo data (CI installs
`--without-demo=all`; the runner now does too). No cron progress on 18: the
fetcher's commit-per-mailbox is a 19 feature, 18 runs the sync in one
transaction.

**Odoo 20, after the first seams** (same day, `tree20` in the scratch
runner: the checkout with `security/` in the `ir.access.csv` shape, 16 rows
built from the 17 ACL rows and the 7 rules). It installs, and 1314 tests
run: 9 failed and 11 errors at the end of the day, down from 412 errors
after the first seam. What the suite found, in the order of how much it
touches:

- `ir.config_parameter` lost `get_param` and `set_param` (typed
  `get_str`/`get_bool`/`set_str`/... instead). 86 call sites in 29 files.
  `compat.py` gives the pair back on the series that lacks it; booleans
  are the catch, because 20's settings page stores `'False'` and reads it
  typed, so a boolean parameter is read through `compat.config_bool`.
- `mail.thread._notify_thread` lost `msg_vals`: the override passes it
  through positionally and names nothing.
- `odoo.http._request_stack` is a contextvar (`http.request_var`) and
  `odoo.http.root.session_store` a function on `odoo.http.session`: two
  helpers in `tests/common.py`, nine call sites.
- `mail.tracking.value` left the `mail` module for an addon of its own
  (`mail_tracking`); the Inbox's tracking rows read a field that may not
  exist. Not seamed yet.
- `res.users.SELF_READABLE_FIELDS` / `SELF_WRITEABLE_FIELDS` are gone;
  20 marks a field `user_writeable=True` at its definition. The module's
  `x_` fields on `res.users` fail with an AccessError until they carry it
  (18 and 19 log an unknown-parameter warning and keep the lists). Not
  seamed yet.
- `mail.message._search` on 20 raises an AccessError for a model the
  reader may not open (`_search_res_access` calls `check_access`); on 18
  and 19 the row is filtered out. Three Inbox tests state the old
  behaviour. A design question, not a rename: decide whether the Inbox
  catches it or the tests change.
- `ir.attachment` internals (`_check_contents`, `LocalBinaryFile`): the
  attachment tests patch something that moved. Not read yet.
- `migrations/` tests assert 19.0 migrations; the folder is empty on the
  `20.0` branch, so they do not apply there.

**What the suite costs per series.** 70 test files, 24k lines, about 80
seconds per full run once Odoo is installed. CI already derives the series
from the manifest (`tools/ci_odoo.sh`, `tools/ci_lint.sh`), so a branch whose
manifest says `18.0.x` tests against `odoo:18.0` without a workflow change.

## The design

### 1. One branch per series, `main` stays the development branch

`18.0`, `19.0` and `20.0` are the release branches, one per series, the
shape Odoo hosts and the OCA expect. `main` stays what it is: the branch
every PR goes to, released to `19.0` by a PR, because 19 is where every
customer is today and the main-to-19.0 gate has earned its keep. A release to
`19.0` is followed by a port to `18.0` and to `20.0` (see 4).

The alternative, no `main` and a PR straight to the series it belongs to,
is the OCA model. It is the right model for a codebase with maintainers per
series. With one person it means deciding per PR which series it is for and
porting the rest by hand anyway; keeping `main` keeps that decision out of
every PR.

### 2. The port is a seam, not a fork

The three branches differ in as few files as possible, and the difference
is written down on `main` and applied by a script, never typed on the branch:

- **`pan_mail_pro/compat.py`** holds every Python name one series spells
  differently, resolved once against the running Odoo. The model files
  import from it and are the same bytes on every branch.
- **`pan_mail_pro/static/src/compat/`** does the same for JS: a file per
  name whose path moved, re-exported, imported by the module as
  `@pan_mail_pro/compat/...`.
- **`tools/series/<series>/`** is what a series needs beyond that: files
  laid over the tree (20.0: `security/ir.access.csv` and the groups-only
  security XML, the 20 path of the chatter), a `REMOVE` list (20.0: the
  ACL csv), a `MANIFEST.sed` (20.0: the security file's name in `data`).
  18.0 needs nothing.
- **`tools/cut_series.sh <series>`** turns a checkout of `main` into the
  series branch: the two manifests' series (the module and the dark-mode CI
  addon), the overlay, the removals, the manifest edits. Cutting a branch
  is running it once on `main`; `tools/odoo_local.sh --series=` and
  `tools/ui_local.sh --series=` run it on a copy, so what they measure is
  what the branch gets.
- **`tools/ci_port_drift.sh`**, a CI job on every branch, archives `main`,
  cuts it for the branch's series and fails on any byte of difference
  (the version's last three numbers aside). So a series branch *is* `main`
  cut, and a change that belongs to one series goes into `tools/series/`
  on `main`, where the next cut and the next merge both see it.

Whether the seam can hold everything is the bet of this plan. What was still
unmeasured when it was written:

- **Constraints: measured, closed.** 18 wants `_sql_constraints = [(name,
  sql, msg)]` on the class; 19 and 20 want `models.Constraint` attributes
  and warn on the old form. `compat.Constraint` is `models.Constraint` where
  it exists; on 18 it is a small class whose `__set_name__` appends to the
  owner's `_sql_constraints`, which 18 reads off the class only when the
  registry is built. The seven model files write `_x = Constraint(...)` and
  are the same bytes on every branch.
- **The 20 access model.** Whether an `ir.access.csv` can express all seven
  rules as they stand (a rule with a domain on `owner_user_id` and
  `mailbox_type`, a rule for personal drafts) and whether `ir.access` rows
  stack the way `ir.rule` did (rules in one group OR together, groups AND).
  Spike: write the file, install, run `tests/test_conversation_api.py` and
  `tests/test_mailbox_permission.py`, which are the tests that state who may
  see what.

### 3. Enterprise

Same branch, same code. CI stays on the community image: Enterprise source
cannot go into GitHub Actions, and a check nobody can run is not a check
(CLAUDE.md). The Enterprise-only paths are already named in TESTPLAN.md
(dark mode, `WebClientEnterprise`, helpdesk routing); that list gets one
column per series and is walked by hand on a release, on the Enterprise
instance of that series. No Enterprise instance for a series means that
series is not released.

### 4. The port routine

After a release to `19.0`:

1. `git merge main` into `18.0`, then into `20.0`. A merge, not a
   cherry-pick: with the seam holding, the only conflicts are inside the
   allowed paths, and `git rerere` replays the ones that repeat.
2. Bump the manifest on that branch (`18.0.<same x.y.z>`), run
   `tools/ci.sh`, push. `release.yml` tags `v18.0.x.y.z` on push to `18.0`.
3. Walk the Enterprise column for that series.

One PR becomes one PR plus two merges, and the measure of whether the seam
holds is how often step 1 conflicts outside the allowed paths. That number
goes in this file after the first ten ports.

### 5. Order

18 first, then 20. 18 has the larger installed base and the smaller delta
(one Python construct, test fixtures, and whatever the Inbox finds). 20
shipped a month ago; its `mail` and `ir.access` internals are the larger
delta and are still moving on the `20.0` branch.

## What is dropped

- **A single branch with runtime detection.** Refused by the loader, see
  above.
- **Odoo 17 and below.** Nobody asked, and the Inbox is built on 18+'s
  `@mail` store.
- **Enterprise in CI.** No source on the runner. Hand-walked per release.
- **A `migrations/` folder per series.** The 19.0 scripts stay on every
  branch: Odoo never runs a folder of another series (above the target on
  18, below the installed version on 20), the lint check only looks at the
  manifest's own series, and the tests that drive those scripts carry
  `@on_series('19.0')` and skip elsewhere. A fresh series has no customer
  database to migrate, so it starts without folders of its own.

## Checklist

Mark a box in the PR that does the work, with the version or the date.

### Step 0. Measure (done 2026-11-03)

- [x] A 19.0 manifest on 18 and 20: refused by the loader
- [x] 18: first blocker, the JS import surface, where `group_ids` lives
- [x] 20: the access model, the mail.message ACL, the moved chatter import
- [x] A no-Docker runner so the next session starts where this one stopped (`tools/odoo_local.sh`)

### Step 1. Infrastructure, on `main`

- [x] `tools/odoo_local.sh`: clone a series, install requirements, run the suite, assert it ran
- [x] `release.yml` tags on push to `18.0`, `19.0` and `20.0`; the release note names the series from the manifest
- [x] `tools/ci_port_drift.sh` and the `port-drift` CI job (2026-11-03); `tools/cut_series.sh` and `tools/series/` with it
- [x] `tests/common.py`: `groups_field()` and `registry_test_mode()`, so no test spells a series (58 lines in 24 files replaced, 2026-11-03)
- [x] Constraints: measured 2026-11-03, no spike needed. `compat.Constraint` is `models.Constraint` where it exists and a `__set_name__` that appends to `_sql_constraints` on 18; the seven model files are identical on every branch
- [x] `tools/ci_lint.sh`: the migration-folder check reads the manifest's series (2026-11-03); the checklist greps are the same on every series
- [ ] CLAUDE.md *Branches* paragraph and the Cloudpepper table: one row per series
- [ ] TESTPLAN.md: the Enterprise checks get a column per series

### Step 2. The `18.0` branch

- [ ] Branch cut from `main`; manifest `18.0.<x.y.z>` (module and CI dark addon); drift check green
- [x] Registry loads on 18 (2026-11-03, `compat.py`: Constraint, Domain, three mail.mail/mail.thread names)
- [x] Installs on 18 (2026-11-03): one view anchor moved to the notebook; `security/` loads unchanged
- [x] Suite green on `tools/odoo_local.sh --series=18.0` (2026-11-03: 1314 tests, 0 failed, 0 errors, from `main` with the seam and no 18-specific file)
- [ ] Suite green on CI's `odoo:18.0` (needs the branch)
- [ ] UI job green: the Inbox renders on 18's `@mail`, every `tools/ui_check.py` assertion
- [ ] Upgrade job: skips cleanly while no `v18.0.*` tag exists, runs once one does
- [ ] Enterprise column walked on an 18 Enterprise instance
- [ ] A Cloudpepper dev instance on an 18 server (servers are pinned per major) with OAuth redirect URIs registered
- [ ] Knowledge base page says which series are supported
- [ ] First tag `v18.0.x.y.z`

### Step 3. The `20.0` branch

- [ ] Branch cut from `main`; manifest `20.0.<x.y.z>` (module and CI dark addon); `security/ir.access.csv`; drift check green
- [x] Spike: `ir.access.csv` holds all 17 ACL rows and 7 rules (2026-11-03, 16 rows, `scripts/`-less: the file is written by hand on the branch); the permission tests that ran pass, the three Inbox access tests are the design question above
- [ ] The mail.message visibility lesson re-measured against `res_access_read`; the "Linked to nothing" folder behaves as on 19; `_search` raising instead of filtering decided
- [x] `static/src/compat/chatter.js`, the 20 path in `tools/series/20.0` (2026-11-03)
- [x] `compat.py`: `get_param`/`set_param` and `config_bool`; `_notify_thread` positional; the two test helpers (2026-11-03)
- [x] Tracking rows without `mail.tracking.value`, `user_writeable=True` on the `res.users` fields, `ir.attachment` without `datas` (`compat.attachment_bytes`, fixtures on `raw`), the Files tab's store payload, `odoo.http` moves in the tests (2026-11-03)
- [ ] Installs; suite green locally, then on CI's `odoo:20.0`
- [ ] UI job green
- [ ] Enterprise column walked on a 20 Enterprise instance
- [ ] Cloudpepper dev instance on a 20 server
- [ ] First tag `v20.0.x.y.z`

### Step 4. The routine

- [ ] First port of a 19.0 release into 18.0 and 20.0 done by the recipe in §4
- [ ] After ten ports: how many conflicted outside the allowed paths, written here
