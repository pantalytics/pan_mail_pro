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
import importlib.util
import os

from odoo.tests import TransactionCase, tagged

from odoo.addons.pan_mail_pro.models import encryption_utils

_MODULE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
_MIGRATION = os.path.join(_MODULE, 'migrations', '19.0.6.0.0')


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
