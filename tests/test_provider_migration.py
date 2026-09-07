# -*- coding: utf-8 -*-
"""Coverage for the 19.0.6.5.0 provider-credential migration.

The upgrade job in CI runs the real thing against a database installed from
the previous release, which is the test that matters for "does it run
cleanly end to end". What it cannot show is the data movement itself: CI's
fresh-from-last-release database never had application credentials filled
in, so the migration's only exercised path there is "nothing to move" — the
same limitation `test_rename_migration.py` and `test_account_migration.py`
work around, and the same fix: load the script and run it against rigged
config parameters directly.
"""
import importlib.util
import os

from odoo.tests import TransactionCase, tagged

_MODULE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
_MIGRATION = os.path.join(_MODULE, 'migrations', '19.0.6.5.0')
_COLLAPSE = os.path.join(_MODULE, 'migrations', '19.0.7.2.0')


def _load(script, folder=_MIGRATION):
    """Pinned to its folder: a migration belongs to its version forever."""
    path = os.path.join(folder, script)
    name = f'pan_provider_{os.path.basename(folder)}_{script[:-3]}'.replace('.', '_')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestProviderMigration(TransactionCase):

    def setUp(self):
        super().setUp()
        self.migration = _load('post-migrate.py')
        self.ICP = self.env['ir.config_parameter'].sudo()
        self.Provider = self.env['pan.mail.provider']

    def test_credentials_and_the_choice_become_a_row(self):
        self.ICP.set_param('pan_mail_pro.microsoft_client_id', 'client-id')
        self.ICP.set_param('pan_mail_pro.microsoft_tenant_id', 'tenant-id')
        self.ICP.set_param('pan_mail_pro.microsoft_client_secret_encrypted', 'cipher-text')
        self.ICP.set_param('pan_mail_pro.setup_provider', 'outlook')

        self.migration.migrate(self.env.cr, '19.0.6.4.1')

        row = self.Provider.search([('provider', '=', 'outlook')])
        self.assertEqual(len(row), 1)
        self.assertEqual(row.client_id, 'client-id')
        self.assertEqual(row.tenant_id, 'tenant-id')
        # Copied as the ciphertext it already is, never decrypted and
        # re-encrypted — same key, same database, so the string is portable
        # as-is (see ARCHITECTURE.md §9.1 and §9.13).
        self.assertEqual(row.client_secret_encrypted, 'cipher-text')
        self.assertEqual(self.Provider.current(), row)

    def test_only_the_chosen_provider_gets_a_row(self):
        """A database can carry two providers' parameters — someone tried
        Microsoft, then went with Google. Mail Pro runs on one provider, so
        only the chosen one becomes a row; the other registration is dropped
        and re-issued from its console if it is ever needed."""
        self.ICP.set_param('pan_mail_pro.microsoft_client_id', 'client-id')
        self.ICP.set_param('pan_mail_pro.microsoft_tenant_id', 'tenant-id')
        self.ICP.set_param('pan_mail_pro.google_client_id', 'google-id')
        self.ICP.set_param('pan_mail_pro.setup_provider', 'gmail')

        self.migration.migrate(self.env.cr, '19.0.6.4.1')

        self.assertFalse(self.Provider.search([('provider', '=', 'outlook')]))
        gmail = self.Provider.search([])
        self.assertEqual(len(gmail), 1)
        self.assertEqual(gmail.provider, 'gmail')
        self.assertEqual(gmail.client_id, 'google-id')

    def test_credentials_with_no_choice_recorded_still_become_the_row(self):
        """A database that predates `setup_provider` says nothing about which
        provider its credentials belong to. One set of them is not ambiguous."""
        self.ICP.set_param('pan_mail_pro.microsoft_client_id', 'client-id')

        self.migration.migrate(self.env.cr, '19.0.6.4.1')

        row = self.Provider.search([])
        self.assertEqual(len(row), 1)
        self.assertEqual(row.provider, 'outlook')

    def test_imap_chosen_with_no_credentials_still_gets_a_row(self):
        """IMAP has no application-credential parameters of its own — the
        only trace of it being chosen is `setup_provider`. Without a row,
        that choice is lost and the database drops back into setup."""
        self.ICP.set_param('pan_mail_pro.setup_provider', 'imap')

        self.migration.migrate(self.env.cr, '19.0.6.4.1')

        row = self.Provider.search([('provider', '=', 'imap')])
        self.assertTrue(row)
        self.assertEqual(self.Provider.current(), row)

    def test_an_untouched_database_gets_no_rows(self):
        self.migration.migrate(self.env.cr, '19.0.6.4.1')

        self.assertFalse(self.Provider.search([]))

    def test_all_six_parameters_are_gone_afterwards(self):
        self.ICP.set_param('pan_mail_pro.microsoft_client_id', 'client-id')
        self.ICP.set_param('pan_mail_pro.setup_provider', 'outlook')

        self.migration.migrate(self.env.cr, '19.0.6.4.1')

        for key in (
            'pan_mail_pro.microsoft_client_id',
            'pan_mail_pro.microsoft_client_secret_encrypted',
            'pan_mail_pro.microsoft_tenant_id',
            'pan_mail_pro.google_client_id',
            'pan_mail_pro.google_client_secret_encrypted',
            'pan_mail_pro.setup_provider',
        ):
            self.assertFalse(self.ICP.get_param(key), f'{key} should have been deleted')

    def test_a_re_run_does_not_duplicate_or_overwrite(self):
        """Idempotent: a provider with a row already is left alone, so a
        retried upgrade cannot clobber a value an admin has since changed."""
        self.ICP.set_param('pan_mail_pro.microsoft_client_id', 'client-id')
        self.ICP.set_param('pan_mail_pro.setup_provider', 'outlook')
        self.migration.migrate(self.env.cr, '19.0.6.4.1')

        row = self.Provider.search([('provider', '=', 'outlook')])
        row.client_id = 'changed-by-admin'
        self.ICP.set_param('pan_mail_pro.microsoft_client_id', 'client-id')  # as if re-run

        self.migration.migrate(self.env.cr, '19.0.6.4.1')

        self.assertEqual(
            self.Provider.search([('provider', '=', 'outlook')]), row)
        self.assertEqual(row.client_id, 'changed-by-admin')


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestProviderCollapse(TransactionCase):
    """19.0.7.2.0: several rows and an `in_use` toggle become one row.

    The rows have to be inserted with SQL: the model refuses a second one now,
    which is the whole point of the migration. The column comes back too — a
    database being upgraded still has it, and this test runs against one that
    does not.
    """

    def setUp(self):
        super().setUp()
        self.migration = _load('post-migrate.py', _COLLAPSE)
        self.Provider = self.env['pan.mail.provider']
        self.env.cr.execute(
            'ALTER TABLE pan_mail_provider ADD COLUMN IF NOT EXISTS in_use bool')

    def _insert(self, provider, in_use, client_id=False):
        self.env.cr.execute(
            """INSERT INTO pan_mail_provider (provider, in_use, client_id)
               VALUES (%s, %s, %s) RETURNING id""",
            (provider, in_use, client_id))
        return self.env.cr.fetchone()[0]

    def test_the_row_in_use_is_the_one_that_survives(self):
        self._insert('outlook', False, 'ms-id')
        keep = self._insert('gmail', True, 'google-id')

        self.migration.migrate(self.env.cr, '19.0.7.1.1')

        self.Provider.invalidate_model()
        rows = self.Provider.search([])
        self.assertEqual(rows.ids, [keep])
        self.assertEqual(rows.provider, 'gmail')

    def test_with_nothing_marked_the_configured_row_survives(self):
        """A database that never went through the picker still has to end up
        with the provider its credentials belong to."""
        self._insert('outlook', False)
        keep = self._insert('gmail', False, 'google-id')

        self.migration.migrate(self.env.cr, '19.0.7.1.1')

        self.Provider.invalidate_model()
        self.assertEqual(self.Provider.search([]).ids, [keep])

    def test_the_column_is_gone_afterwards(self):
        self._insert('outlook', True, 'ms-id')

        self.migration.migrate(self.env.cr, '19.0.7.1.1')

        self.env.cr.execute("""
            SELECT 1 FROM information_schema.columns
             WHERE table_name = 'pan_mail_provider' AND column_name = 'in_use'
        """)
        self.assertFalse(self.env.cr.fetchone())

    def test_one_row_is_left_alone(self):
        keep = self._insert('imap', True)

        self.migration.migrate(self.env.cr, '19.0.7.1.1')

        self.Provider.invalidate_model()
        self.assertEqual(self.Provider.search([]).ids, [keep])
