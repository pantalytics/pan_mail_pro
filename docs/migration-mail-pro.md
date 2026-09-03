# Migrating an existing database to Mail Pro

The module's technical name changed from `pan_outlook_pro` to `pan_mail_pro`
in version 19.0.3.0.0. Fresh installs need nothing from this page. Databases
that already run the module need the rename applied to their data first.

At the time of writing that is one database: Juffermans.

## Why order matters

Odoo finds a module's migration scripts by locating the module on disk. After
the rename there is no `pan_outlook_pro` directory left, so Odoo does not see
an upgrade at all — it sees a brand new module called `pan_mail_pro` and
installs it fresh, next to the old module's records. You get duplicated views,
a module stuck in a broken state, and menu entries pointing at nothing.

So the data has to be renamed **before** Odoo ever starts with the new code.

## What is not renamed by this script

Two things keep their old names here, and are renamed later by an ordinary
Odoo migration instead:

- **`ir.config_parameter` keys `x_pan_outlook_pro.*`.** These hold the Fernet
  encryption key and the encrypted Azure and Google secrets. If the code looked
  for a key that did not exist, `get_encryption_key()` would quietly generate a
  **new** one — and every stored OAuth token and client secret would become
  permanently undecryptable. So this script leaves them alone; the 19.0.6.0.0
  module migration renames them to `pan_mail_pro.*` in the same transaction
  as the code that reads the new names, and `get_encryption_key()` adopts a
  key still stored under the old name rather than minting a new one.
- **XML record ids** such as `mail_server_disabled`, and the
  `invalid.outlook-pro.disabled` sentinel mail server host that `__init__.py`
  filters on by value.

Both can be renamed later as isolated changes with their own migrations.

## Runbook

1. **Back up.** Not optional — the SQL below is not reversible without it.

   ```
   create_backup(instance_id = <production instance>)
   ```

2. **Rehearse on staging.** Restore that backup into the staging instance and
   run steps 3 to 6 there first. Confirm sending, inbound sync and the OAuth
   flow all still work before touching production.

3. **Stop Odoo** on the target instance. The script must not race the registry
   loading.

4. **Run the rename.**

   ```
   psql -d <database> -v ON_ERROR_STOP=1 -f tools/rename_to_mail_pro.sql
   ```

   The script ends with two checks. Every `leftover` count must be `0`, and the
   final query must return exactly one row for `pan_mail_pro` in state
   `installed`. If anything else comes back, stop and restore the backup.

5. **Deploy the new code**, either through the suite (see
   `odoo-pantalytics-suite`) or by updating the module source directly.

6. **Start Odoo and upgrade the module.**

   ```
   odoo-bin -u pan_mail_pro
   ```

   The manifest version moved to 19.0.3.0.0, so Odoo will run the upgrade.

7. **Verify.**
   - `list_installed_modules` shows `pan_mail_pro` and no `pan_outlook_pro`.
   - Settings → Mail Pro opens and still shows the Azure configuration —
     this is the check that the configuration parameters survived (they are
     still `x_pan_outlook_pro.*` at this point; 19.0.6.0.0 renames them).
   - Send a test mail from a shared mailbox.
   - Complete an OAuth flow. This exercises the `pan_mail_pro.oauth_result`
     template, which is what the `ir_ui_view.key` update in the script fixes.

## If it goes wrong

Restore the backup from step 1 and redeploy the previous module version. The
rename touches identity columns rather than business data, so a restore returns
you cleanly to the starting state — provided you did step 1.

## Juffermans on CloudPepper — the concrete plan

State on 2026-09-03, read from the CloudPepper API:

| | production | staging |
|---|---|---|
| instance | `juffermans.cloudpepper.site` | `staging-i5k88ysv55d.cloudpepper.site` |
| instance id | `019f1d30-a256-75f0-89aa-6e17590e10f5` | `019f1d39-9ffb-7f27-aede-4e6c82a54eb4` |
| module in `ir.module.module` | `pan_outlook_pro` 19.0.1.2.0 | `pan_outlook_pro` 19.0.1.2.0 |
| addon source | `pan_outlook_pro.git` @ `19.0-prod-rollback` | `pan_outlook_pro.git` @ `19.0` |
| module id | `019f22b4-7280-724b-a481-6be1f4e73104` | `019f22b4-7280-724b-a481-6be1f4e73104` |

Target: `pan_mail_pro` 19.0.6.3.0 (branch `19.0`).

### About the old GitHub URL

The repository was renamed on GitHub, so `pan_outlook_pro.git` is a redirect to
`pan_mail_pro.git` and CloudPepper keeps pulling the right code. It is cosmetic
and it is *not* the upgrade: repointing the URL does not rename anything in the
database. Repoint it anyway when the source is next touched, so the addons list
stops lying about which repo it tracks.

### Staging is already in the broken half-state

Staging tracks branch `19.0`, so its disk carries the module directory
`pan_mail_pro` while its database still has `pan_outlook_pro` installed. Odoo
sees an installed module that is missing on disk and a new module that is not
installed. Mail is not working there and no amount of `-u` fixes it — only the
rename SQL does. Do not read staging's current behaviour as a preview of
production.

### The step production actually takes

Production sits on 19.0.1.2.0, so a single `-u pan_mail_pro` crosses seven
migration folders (19.0.2.1.0 through 19.0.6.3.0) on top of the SQL rename.
Nothing in CI covers that combination against real data; `ci_rename_rehearsal.sh`
covers the chain from a fresh baseline, where every backfill moves zero rows.
The rehearsal on a restored production backup is therefore the only real test,
and it is not optional.

### What no tool can do for us

`tools/rename_to_mail_pro.sql` needs `psql` on the instance with Odoo stopped.
The CloudPepper MCP exposes backups, module pulls, restarts, index and Postgres
tuning — but no arbitrary SQL. So step 3 below is a shell on the server (SSH or
the CloudPepper dashboard), by hand, both times.

### Runbook

1. Back up production (`create_backup`, note it `pre-mail-pro-rename`).
2. Restore that backup into staging, neutralized. Staging is now a true copy of
   the pre-rename production database.
3. On staging: stop Odoo, run `tools/rename_to_mail_pro.sql`, check that every
   `leftover` count is 0 and `pan_mail_pro` comes back `installed`.
4. On staging: pull branch `19.0` and run `-u pan_mail_pro`. Read the migration
   log — this is the run where the backfills touch real rows, so a row count of
   0 is a finding, not a pass.
5. Verify on staging: Settings opens with the Azure configuration intact (proves
   the config parameters survived 19.0.6.0.0), a test send from a shared mailbox
   works, incoming sync lands a mail, and one full OAuth round trip completes.
6. Only then production, same three steps in a quiet window: stop, rename SQL,
   pull `19.0`, `-u pan_mail_pro`, same five checks.
7. Repoint the addon source to `https://github.com/pantalytics/pan_mail_pro.git`
   and decide the webhook / auto-upgrade flags deliberately. Production is
   currently pinned to `19.0-prod-rollback`; leaving auto-upgrade off until the
   upgrade is verified is the safer default.

Rollback at any point is the step 1 backup. The rename touches identity columns,
not business data, so a restore returns cleanly to the starting state.
