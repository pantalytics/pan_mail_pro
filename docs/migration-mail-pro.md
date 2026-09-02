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
