# Deploying on Cloudpepper

Internal runbook. Not part of the published GitBook.

Cloudpepper has **two independent update tracks** and they are not
interchangeable:

| Track | What moves | Trigger | Runs `-u`? |
|-------|-----------|---------|------------|
| **Odoo core** | the framework at `/var/odoo/<inst>/src` | server autoUpdate (Sun/Mon 10:00 CET) or `update_instance_odoo` | **No** |
| **Addons** | our git repo in `extra-addons/` | push to the tracked branch (webhook), or `update_module_branch` | Only if `auto_upgrade` is on |

Neither track migrates the database on its own. That gap is where the
white screens come from.

## The order, always

1. **Backup.** `create_backup` on anything that is not a throwaway.
2. **Odoo core first.** A core update swaps the framework and restarts.
   It leaves the database on the old schema.
3. **Then the addons.** Push, or `update_module_branch`, so the module
   code matches the core it now runs on.
4. **Then the upgrade.** `-u pan_mail_pro` (auto_upgrade, or the Apps UI).
   This is the step that rewrites views, assets and schema.
5. **Restart** and load one page before calling it done.

Reversing 2 and 3 gives you a module built for the new core running on
the old one, or the other way round. Both look like a white screen.

## Why the white screen

Odoo only runs `-u` when `__manifest__.py`'s version is **higher** than
the version in `ir.module.module`. Forget the bump and Cloudpepper pulls
the code, restarts, and serves the old views and the old asset bundles
against new Python. The page renders empty and the log is clean.

So: **every push that touches a view, an asset or a field needs a version
bump.** Python-only changes survive on the restart alone, but the bump
costs nothing and removes the judgement call.

The other two causes, in order of how often they hit:

- **Stale asset attachments.** The bundle in the database no longer matches
  the files. Symptom is a white screen with 404s on `/web/assets/...`.
  ```sql
  DELETE FROM ir_attachment WHERE url LIKE '/web/assets/%';
  ```
  then restart. Odoo regenerates them.
- **A core update with no module upgrade after it.** Same fix as step 4.

## Flags per instance

`set_module_auto_deploy` carries two independent switches:

- `webhook` -- push re-pulls the code. On everywhere.
- `auto_upgrade` -- the re-pull also runs `-u <module>`. On for dev and
  staging. On production it means every push runs a live migration, so
  turn it on only where you accept that.

`mailpro-dev` runs both on, tracking `19.0`. That is the point of it: push,
wait a minute, click through a real OAuth consent screen.

## Server autoUpdate

The server applies OS packages Sunday and Odoo packages Monday, both 10:00
CET. Security updates are always applied regardless. So the core can move
under you on a Monday morning without anyone deciding to move it -- and
nothing runs `-u` afterwards. If an instance goes white on a Monday, run
step 4 before debugging anything else.

## Checking, not guessing

| Question | Call |
|---|---|
| what core version is this server on | `get_server_version` |
| what code is deployed | `list_managed_modules` (repo + branch) |
| what is installed in the database | `list_installed_modules` (version per module) |
| did it break | `find_log_errors`, then `tail_instance_log` |

The first two disagreeing with the third is the diagnosis, nearly always.
