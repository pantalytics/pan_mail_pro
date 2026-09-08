# -*- coding: utf-8 -*-
"""Coverage for the 19.0.6.0.0 rename migration.

The upgrade job in CI runs the real thing against a database installed from
the previous release, which is the test that matters. What it cannot show is
the data movement in isolation, or what happens when the scripts meet a
database that is already renamed (a retried upgrade) — so the scripts are
loaded here and run against real rows, the way `test_account_migration` does
for the token migration.

The legacy column has to be recreated first: CI installs fresh and never had
it. The fixture adds it with raw SQL, the way an upgraded database still
carries it before the post-migrate drops it.
"""
import ast
import importlib.util
import os
import re

from odoo.tests import TransactionCase, tagged

from odoo.addons.pan_mail_pro.models import encryption_utils

_MODULE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
_MIGRATION = os.path.join(_MODULE, 'migrations', '19.0.6.0.0')
_RENAME_SQL = os.path.join(_MODULE, 'tools', 'rename_to_mail_pro.sql')

_STALE = 'Outlook Pro - Microsoft 365 Email Integration'


def _load(script):
    """Pinned to its own folder: this migration belongs to 19.0.6.0.0 forever."""
    path = os.path.join(_MIGRATION, script)
    spec = importlib.util.spec_from_file_location(f'pan_rename_{script[:-3]}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestRenameMigration(TransactionCase):

    def setUp(self):
        super().setUp()
        # Mail Pro refuses to create a mailbox while the internal domain
        # list is empty. A domain nothing in this fixture uses, so the gate
        # opens without turning any fixture address internal.
        self.env['pan.mail.domain'].set_domains(['gate-fixture.test'])
        self.pre = _load('pre-migrate.py')
        self.post = _load('post-migrate.py')
        self.ICP = self.env['ir.config_parameter'].sudo()

    def _column_exists(self, table, column):
        self.env.cr.execute("""
            SELECT 1 FROM information_schema.columns
             WHERE table_name = %s AND column_name = %s
        """, (table, column))
        return bool(self.env.cr.fetchone())

    # -- pre-migrate -------------------------------------------------------- #

    def test_pre_migrate_is_a_no_op_on_a_renamed_database(self):
        """A retried upgrade must find nothing to do and break nothing."""
        before = self.env['pan.mail.mailbox'].search_count([])
        self.pre.migrate(self.env.cr, '19.0.5.6.0')
        self.env.invalidate_all()
        self.assertEqual(self.env['pan.mail.mailbox'].search_count([]), before)
        self.assertTrue(self.env['ir.model']._get('pan.mail.mailbox'))
        self.assertTrue(self.env['ir.model']._get('pan.mail.fetcher'))
        self.assertTrue(self.env.ref('pan_mail_pro.model_pan_mail_mailbox'))

    def test_pre_migrate_renames_config_parameters(self):
        """Every `x_pan_outlook_pro.*` key moves under `pan_mail_pro.`."""
        self.env.cr.execute("""
            DELETE FROM ir_config_parameter
             WHERE key IN ('x_pan_outlook_pro.tenant_id', 'pan_mail_pro.microsoft_tenant_id')
        """)
        self.env.cr.execute("""
            INSERT INTO ir_config_parameter (key, value)
            VALUES ('x_pan_outlook_pro.tenant_id', 'tenant-from-before')
        """)
        self.pre.rename_params(self.env.cr)
        self.env.invalidate_all()
        self.assertEqual(self.ICP.get_param('pan_mail_pro.microsoft_tenant_id'), 'tenant-from-before')
        self.assertFalse(self.ICP.get_param('x_pan_outlook_pro.tenant_id'))

    def test_pre_migrate_keeps_an_existing_new_parameter(self):
        """Never overwrite a value already stored under the new key."""
        self.env.cr.execute("""
            DELETE FROM ir_config_parameter
             WHERE key IN ('x_pan_outlook_pro.setup_provider', 'pan_mail_pro.setup_provider')
        """)
        self.env.cr.execute("""
            INSERT INTO ir_config_parameter (key, value)
            VALUES ('x_pan_outlook_pro.setup_provider', 'outlook'),
                   ('pan_mail_pro.setup_provider', 'gmail')
        """)
        self.pre.rename_params(self.env.cr)
        self.env.invalidate_all()
        self.assertEqual(self.ICP.get_param('pan_mail_pro.setup_provider'), 'gmail')

    def test_pre_migrate_renames_an_xmlid_once(self):
        """The record keeps its identity; a second run leaves it alone."""
        self.env.cr.execute("""
            UPDATE ir_model_data SET name = 'action_microsoft_mailbox'
             WHERE module = 'pan_mail_pro' AND name = 'action_pan_mail_mailbox'
        """)
        action_id = self.env.ref('pan_mail_pro.action_microsoft_mailbox').id
        self.pre.rename_xmlids(self.env.cr)
        self.pre.rename_xmlids(self.env.cr)
        self.env.invalidate_all()
        self.assertEqual(self.env.ref('pan_mail_pro.action_pan_mail_mailbox').id, action_id)

    # -- post-migrate ------------------------------------------------------- #

    def _message_with_legacy_id(self, wire_id, message_id='<odoo-generated@example.com>'):
        self.env.cr.execute(
            'ALTER TABLE mail_message ADD COLUMN IF NOT EXISTS x_microsoft_message_id varchar')
        message = self.env['mail.message'].create({
            'model': 'res.partner',
            'res_id': self.env.user.partner_id.id,
            'body': 'sent from Odoo, long ago',
            'message_id': message_id,
        })
        self.env.cr.execute(
            'UPDATE mail_message SET x_microsoft_message_id = %s WHERE id = %s',
            (wire_id, message.id))
        return message

    def test_post_migrate_moves_legacy_wire_ids_into_the_ref_index(self):
        """The id a recipient replies to must stay resolvable after the column goes."""
        message = self._message_with_legacy_id('<minted-by-graph@outlook.com>')

        self.post.migrate(self.env.cr, '19.0.5.6.0')
        self.env.invalidate_all()

        self.assertFalse(self._column_exists('mail_message', 'x_microsoft_message_id'))
        self.assertFalse(self._column_exists('mail_mail', 'x_microsoft_message_id'))
        self.assertEqual(
            self.env['pan.mail.message.ref'].lookup('<minted-by-graph@outlook.com>'), message)
        self.assertEqual(
            self.env['pan.mail.matcher']._resolve_message_id('<minted-by-graph@outlook.com>'),
            message)

    def test_post_migrate_skips_ids_already_indexed_or_native(self):
        """No duplicate rows: neither for an indexed id nor for Odoo's own."""
        message = self._message_with_legacy_id('<already@outlook.com>')
        self.env['pan.mail.message.ref'].record(message, '<already@outlook.com>')
        native = self._message_with_legacy_id('<same-as-native@example.com>',
                                              message_id='<same-as-native@example.com>')

        self.post.migrate(self.env.cr, '19.0.5.6.0')
        self.env.invalidate_all()

        Ref = self.env['pan.mail.message.ref']
        self.assertEqual(Ref.search_count([('mail_message_id', '=', message.id)]), 1)
        self.assertEqual(Ref.search_count([('mail_message_id', '=', native.id)]), 0)

    def test_post_migrate_is_a_no_op_without_the_column(self):
        self.assertFalse(self._column_exists('mail_message', 'x_microsoft_message_id'))
        self.post.migrate(self.env.cr, '19.0.5.6.0')  # must not raise

    # -- the key ------------------------------------------------------------ #

    def test_encryption_key_is_adopted_from_its_old_name(self):
        """Code running ahead of the migration must not mint a new key."""
        self.ICP.set_param(encryption_utils.AUTO_KEY_PARAM, False)
        self.ICP.set_param(encryption_utils.LEGACY_KEY_PARAM, 'legacy-key-bytes')
        try:
            key = encryption_utils.get_encryption_key(self.env)
        finally:
            # Other tests in this transaction encrypt with the real key.
            self.ICP.set_param(encryption_utils.LEGACY_KEY_PARAM, False)
            self.ICP.set_param(encryption_utils.AUTO_KEY_PARAM, False)
        self.assertEqual(key, b'legacy-key-bytes')

    # -- tools/rename_to_mail_pro.sql --------------------------------------- #
    #
    # The Python migrations above had tests; the SQL script had none, and that
    # is precisely where it broke. Its metadata statement assigned a bare string
    # to `shortdesc` — `jsonb` since Odoo 17 — so it raised `invalid input
    # syntax for type json` and, under the `ON_ERROR_STOP=1` the runbook
    # prescribes, aborted the whole rename. The repair it documents had never
    # once run. These tests execute the real file.

    def _run_rename_sql(self):
        """Execute the shipped script inside the test transaction.

        `BEGIN;`/`COMMIT;` are dropped rather than the statements rewritten:
        committing here would escape the rollback every other test relies on.
        Everything that touches a row still runs exactly as shipped.
        """
        with open(_RENAME_SQL) as handle:
            script = handle.read()
        script = re.sub(r'(?im)^\s*(BEGIN|COMMIT)\s*;\s*$', '', script)
        self.env.cr.execute(script)

    def _shortdesc(self):
        self.env.cr.execute(
            "SELECT shortdesc, summary FROM ir_module_module WHERE name = 'pan_mail_pro'")
        return self.env.cr.fetchone()

    def _make_stale(self):
        """A database that took the rename while its users read en_GB.

        Exactly the shape production was found in: the source language already
        refreshed from the manifest, a second language still on the old name.
        """
        self.env.cr.execute("""
            UPDATE ir_module_module
               SET shortdesc = jsonb_build_object('en_US', 'Mail Pro - Email Integration',
                                                  'en_GB', %s),
                   summary   = jsonb_build_object('en_US', 'whatever the manifest says',
                                                  'en_GB', 'Outlook Pro, via Graph API')
             WHERE name = 'pan_mail_pro'
        """, (_STALE,))

    def test_rename_sql_runs_against_jsonb_columns(self):
        """The script must not raise. It did, on every Odoo 17+ database."""
        self._make_stale()
        self._run_rename_sql()  # must not raise

    def test_rename_sql_clears_a_stale_translation(self):
        """An apps-list refresh cannot fix this, so the script has to.

        `update_list()` rewrites the source language and leaves other keys
        alone, so en_GB kept advertising "Outlook Pro" indefinitely. Asserting
        on the raw json rather than through a language context keeps the test
        independent of which languages CI happens to have active.
        """
        self._make_stale()

        self._run_rename_sql()

        shortdesc, summary = self._shortdesc()
        self.assertNotIn('Outlook Pro', str(shortdesc))
        self.assertNotIn('Outlook Pro', str(summary))
        self.assertEqual(shortdesc.get('en_US'), 'Mail Pro - Email Integration')
        self.assertNotIn(
            'en_GB', shortdesc,
            "the stale key must be dropped so en_GB falls back to the source term")

    def test_rename_sql_matches_the_manifest(self):
        """The copy the script writes is the copy the manifest declares.

        Without this the two drift the moment somebody edits `__manifest__.py`,
        and the script starts restoring a name that is itself out of date.
        """
        with open(os.path.join(_MODULE, '__manifest__.py')) as handle:
            manifest = ast.literal_eval(handle.read())
        self._make_stale()

        self._run_rename_sql()

        shortdesc, summary = self._shortdesc()
        self.assertEqual(shortdesc.get('en_US'), manifest['name'])
        self.assertEqual(summary.get('en_US'), manifest['summary'])

    def test_rename_sql_leaves_a_clean_row_alone(self):
        """Guarded on stale content, so a repeat run is a no-op.

        The runbook tells the reader to re-run the script when in doubt, which
        is only safe advice while this holds.
        """
        self.env.cr.execute("""
            UPDATE ir_module_module
               SET shortdesc = jsonb_build_object('en_US', 'Mail Pro - Email Integration',
                                                  'nl_NL', 'Mail Pro - E-mailkoppeling')
             WHERE name = 'pan_mail_pro'
        """)

        self._run_rename_sql()

        shortdesc, _summary = self._shortdesc()
        self.assertEqual(shortdesc.get('nl_NL'), 'Mail Pro - E-mailkoppeling',
                         'a real translation must survive a re-run')
