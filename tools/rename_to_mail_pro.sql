-- Rename pan_outlook_pro -> pan_mail_pro in an existing database.
--
-- Run this ONCE per database, with Odoo STOPPED, BEFORE deploying the renamed
-- code. See docs/migration-mail-pro.md for the full runbook.
--
--   psql -d <database> -v ON_ERROR_STOP=1 -f tools/rename_to_mail_pro.sql
--
-- Why this is not an Odoo migration script: Odoo runs a module's migrations by
-- finding that module on disk. After the rename there is no `pan_outlook_pro`
-- directory left to find, so Odoo would treat `pan_mail_pro` as a brand new
-- module and install it fresh alongside the old records. The rename has to
-- happen before Odoo loads its registry.
--
-- Deliberately NOT renamed:
--   * ir_config_parameter keys `x_pan_outlook_pro.*` - they hold the Fernet
--     encryption key and the encrypted OAuth secrets. The code still reads
--     them under the old names; renaming them is a separate change with its
--     own risk.
--   * XML record ids (mail_server_invalid_outlook_pro and friends) and the
--     `invalid.outlook-pro.disabled` sentinel mail server host, which
--     __init__.py filters on by value.

BEGIN;

-- The module itself.
UPDATE ir_module_module
   SET name = 'pan_mail_pro'
 WHERE name = 'pan_outlook_pro';

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
  FROM ir_ui_view                  WHERE key LIKE 'pan_outlook_pro.%';

-- And this one must return exactly one row, state 'installed'.
SELECT name, state, latest_version
  FROM ir_module_module
 WHERE name = 'pan_mail_pro';
