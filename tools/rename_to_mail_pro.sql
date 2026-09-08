-- Rename pan_outlook_pro -> pan_mail_pro in an existing database.
--
-- Run this ONCE per database, with Odoo STOPPED, BEFORE deploying the renamed
-- code. See docs/migration-mail-pro.md for the full runbook. Every statement is
-- idempotent, so re-running it on a database that already took the rename is
-- safe -- and is the repair for one that took an earlier version of this script,
-- which renamed the module but left the Apps screen saying "Outlook Pro".
--
--   psql -d <database> -v ON_ERROR_STOP=1 -f tools/rename_to_mail_pro.sql
--
-- Why this is not an Odoo migration script: Odoo runs a module's migrations by
-- finding that module on disk. After the rename there is no `pan_outlook_pro`
-- directory left to find, so Odoo would treat `pan_mail_pro` as a brand new
-- module and install it fresh alongside the old records. The rename has to
-- happen before Odoo loads its registry.
--
-- Deliberately NOT renamed here:
--   * ir_config_parameter keys `x_pan_outlook_pro.*` - they hold the Fernet
--     encryption key and the encrypted OAuth secrets. The 19.0.6.0.0 module
--     migration renames them together with the code that reads the new names.
--   * XML record ids: the 19.0.6.0.0 migration renames those too, in place.
--   * The `invalid.outlook-pro.disabled` sentinel mail server host: a stored
--     value on the placeholder server, harmless and never read by code.

BEGIN;

-- The module itself.
UPDATE ir_module_module
   SET name = 'pan_mail_pro'
 WHERE name = 'pan_outlook_pro';

-- The copy the Apps screen shows. `shortdesc`, `summary` and `description` are
-- read from `__manifest__.py`, but only when Odoo runs `update_list()` — an
-- "Update Apps List", or a start with `-i`/`-u`. A host that starts Odoo without
-- either (odoo.sh does exactly that when the module is not installed in that
-- database) never refreshes them, so renaming `name` alone leaves the Apps
-- screen advertising "Outlook Pro" under the technical name `pan_mail_pro`,
-- indefinitely.
--
-- An apps-list refresh is NOT the whole answer either, which is the trap this
-- statement exists to spring. These three are *translated* columns, `jsonb`
-- since Odoo 17. `update_list()` writes the manifest term into the **source**
-- language and leaves every other key alone, so a database whose users read
-- `en_GB` — or any language that picked up a value while the module was still
-- called Outlook Pro — keeps being told "Outlook Pro" no matter how often the
-- list is refreshed. Only rewriting the whole json value fixes it.
--
-- Hence `jsonb_build_object` rather than a bare string: assigning
-- `'Mail Pro - Email Integration'` to a `jsonb` column coerces an untyped
-- literal to json, and a bare unquoted scalar is not valid json, so the
-- statement raises `invalid input syntax for type json` and — under the
-- `ON_ERROR_STOP=1` the runbook prescribes — takes the whole rename down with
-- it. Same reason the guard below compares `::text` instead of `coalesce(x,'')`:
-- there is no `LIKE` operator for jsonb.
--
-- Dropping the non-source keys is deliberate. The guard only fires on a row
-- that still mentions "Outlook Pro" somewhere, so every key it discards was the
-- old name rather than a real translation, and each language falls back to the
-- source term until Odoo refills it.
--
-- Keyed on the *new* name and guarded on stale content, so this also repairs a
-- database that already ran an earlier version of this script, and is a no-op
-- on one Odoo has since refreshed. `description` is cleared rather than copied:
-- 90 lines of manifest prose inlined here would be a second copy that goes
-- stale. Odoo refills all three at the next apps-list refresh, which for an
-- installed module is step 6 of the runbook.
UPDATE ir_module_module
   SET shortdesc   = jsonb_build_object('en_US', 'Mail Pro - Email Integration'),
       summary     = jsonb_build_object('en_US', 'Microsoft 365, Gmail or IMAP/SMTP: send from any mailbox, sync incoming mail into the chatter, thread replies properly'),
       description = NULL
 WHERE name = 'pan_mail_pro'
   AND (shortdesc::text   LIKE '%Outlook Pro%'
     OR summary::text     LIKE '%Outlook Pro%'
     OR description::text LIKE '%Outlook Pro%');

-- Every XML id the module owns (views, menus, actions, access rules, crons).
UPDATE ir_model_data
   SET module = 'pan_mail_pro'
 WHERE module = 'pan_outlook_pro';

-- Other modules declaring a dependency on it.
UPDATE ir_module_module_dependency
   SET name = 'pan_mail_pro'
 WHERE name = 'pan_outlook_pro';

-- QWeb templates carry the full `module.name` in their own `key` column, not
-- just in ir_model_data. Miss this and `t-call="pan_mail_pro.oauth_result"`
-- raises a template-not-found at runtime, which only shows up when a user
-- completes an OAuth flow.
UPDATE ir_ui_view
   SET key = replace(key, 'pan_outlook_pro.', 'pan_mail_pro.')
 WHERE key LIKE 'pan_outlook_pro.%';

COMMIT;

-- Asset bundles need no attention here: Odoo invalidates and regenerates them
-- when the module is upgraded.

-- Verification. Every count below must be 0.
SELECT 'ir_module_module'            AS table_name, count(*) AS leftover
  FROM ir_module_module            WHERE name = 'pan_outlook_pro'
UNION ALL
SELECT 'ir_model_data',              count(*)
  FROM ir_model_data               WHERE module = 'pan_outlook_pro'
UNION ALL
SELECT 'ir_module_module_dependency', count(*)
  FROM ir_module_module_dependency WHERE name = 'pan_outlook_pro'
UNION ALL
SELECT 'ir_ui_view.key',             count(*)
  FROM ir_ui_view                  WHERE key LIKE 'pan_outlook_pro.%'
UNION ALL
SELECT 'ir_module_module.terp',    count(*)
  FROM ir_module_module            WHERE name = 'pan_mail_pro'
                                     AND (shortdesc::text   LIKE '%Outlook Pro%'
                                       OR summary::text     LIKE '%Outlook Pro%'
                                       OR description::text LIKE '%Outlook Pro%');

-- And this one must return exactly one row, state 'installed'.
SELECT name, state, latest_version
  FROM ir_module_module
 WHERE name = 'pan_mail_pro';
