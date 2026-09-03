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


def _load(script):
    """Pinned to its own folder: this migration belongs to 19.0.6.5.0 forever."""
    path = os.path.join(_MIGRATION, script)
    spec = importlib.util.spec_from_file_location(f'pan_provider_{script[:-3]}', path)
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
        self.assertTrue(row.in_use)

    def test_a_configured_but_unchosen_provider_keeps_its_credentials(self):
        """An admin can hold two providers' credentials at once — the whole
        point of a row per provider — so the migration must not drop the one
        that was not the active choice."""
        self.ICP.set_param('pan_mail_pro.microsoft_client_id', 'client-id')
        self.ICP.set_param('pan_mail_pro.microsoft_tenant_id', 'tenant-id')
        self.ICP.set_param('pan_mail_pro.google_client_id', 'google-id')
        self.ICP.set_param('pan_mail_pro.setup_provider', 'gmail')

        self.migration.migrate(self.env.cr, '19.0.6.4.1')

        outlook = self.Provider.search([('provider', '=', 'outlook')])
        gmail = self.Provider.search([('provider', '=', 'gmail')])
        self.assertTrue(outlook)
        self.assertFalse(outlook.in_use)
        self.assertTrue(gmail.in_use)

    def test_imap_chosen_with_no_credentials_still_gets_a_row(self):
        """IMAP has no application-credential parameters of its own — the
        only trace of it being chosen is `setup_provider`. Without a row,
        that choice is lost and the database drops back into setup."""
        self.ICP.set_param('pan_mail_pro.setup_provider', 'imap')

        self.migration.migrate(self.env.cr, '19.0.6.4.1')

        row = self.Provider.search([('provider', '=', 'imap')])
        self.assertTrue(row)
        self.assertTrue(row.in_use)

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
