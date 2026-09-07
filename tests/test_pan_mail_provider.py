# -*- coding: utf-8 -*-
"""Guards `pan.mail.provider`: which credentials count as complete per
provider, that switching keeps what you switch away from, and that a secret
is never handed back once it is saved.
"""
from psycopg2 import IntegrityError

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestProviderCredentials(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Provider = cls.env['pan.mail.provider']

    def test_microsoft_needs_a_tenant(self):
        """Azure needs a tenant; a client id and secret alone are not setup."""
        row = self.Provider.create({
            'provider': 'outlook', 'client_id': 'id', 'client_secret': 'secret',
        })
        self.assertFalse(row.credentials_set)

        row.tenant_id = 'tenant'
        self.assertTrue(row.credentials_set)

    def test_google_needs_no_tenant(self):
        """Google has no tenant, so the same flag must not demand one."""
        row = self.Provider.create({
            'provider': 'gmail', 'client_id': 'id', 'client_secret': 'secret',
        })
        self.assertTrue(row.credentials_set)

    def test_imap_is_measured_by_its_accounts(self):
        """IMAP has no application registration, so "configured" and
        "connected" both read the accounts instead of a credential field.

        `credentials_set`/`connected` read a different model, which Odoo has
        no way to know changed — `invalidate_recordset()` between an account
        edit and the next read is the same thing a fresh page load would do.
        """
        row = self.Provider.create({'provider': 'imap'})
        self.assertFalse(row.credentials_set)

        account = self.env['pan.mail.account'].create({
            'email': 'setup@company.test', 'provider': 'imap',
            'imap_host': 'imap.soverin.net', 'smtp_host': 'smtp.soverin.net',
        })
        row.invalidate_recordset()
        self.assertTrue(row.credentials_set)
        # An account without a password is configured but not usable.
        self.assertFalse(row.connected)

        account.password = 'hunter2'
        row.invalidate_recordset()
        self.assertTrue(row.connected)

    def test_a_second_provider_is_refused(self):
        """Mail Pro runs on one provider. A second row would sit there
        looking configured while `current()` reads the first."""
        self.Provider.create({'provider': 'outlook'})
        with self.assertRaises(ValidationError):
            self.Provider.create({'provider': 'gmail'})

    def test_current_is_the_row(self):
        """The one question the table answers, asked in one place."""
        self.assertFalse(self.Provider.current())
        row = self.Provider.create({'provider': 'gmail'})
        self.assertEqual(self.Provider.current(), row)

    def test_switching_provider_is_editing_the_row(self):
        row = self.Provider.create({
            'provider': 'outlook',
            'client_id': 'id', 'client_secret': 'secret', 'tenant_id': 'tenant',
        })
        row.provider = 'gmail'
        self.assertEqual(self.Provider.current().provider, 'gmail')

    def test_the_secret_is_never_handed_back(self):
        row = self.Provider.create({
            'provider': 'gmail', 'client_id': 'id', 'client_secret': 'secret',
        })
        self.assertEqual(row.client_secret, '********')

    def test_the_placeholder_does_not_overwrite_the_stored_secret(self):
        """Re-saving the placeholder is a no-op — the mechanism that lets the
        form round-trip without the admin retyping an unrelated field."""
        row = self.Provider.create({
            'provider': 'gmail', 'client_id': 'id', 'client_secret': 'secret',
        })
        stored = row.client_secret_encrypted

        row.client_secret = '********'

        self.assertEqual(row.client_secret_encrypted, stored)

    def test_no_credential_fields_for_imap(self):
        """IMAP has no application registration to speak of."""
        row = self.Provider.create({'provider': 'imap'})
        self.assertFalse(row.uses_oauth)
        self.assertFalse(row.redirect_uri)

    def test_the_fields_are_labelled_the_way_the_console_labels_them(self):
        """Azure calls them Application (client) ID, Client Secret Value and
        Directory (tenant) ID. A form that renames them makes the admin
        translate while copying, which is how a secret ends up in the tenant
        box. Google's console uses its own two names and has no tenant."""
        arch = self.env['pan.mail.provider'].get_view(
            self.env.ref('pan_mail_pro.view_pan_mail_provider_form').id, 'form')['arch']
        for label in ('Application (client) ID', 'Client Secret Value',
                      'Directory (tenant) ID'):
            with self.subTest(label=label):
                self.assertIn(label, arch)
        # And the Secret ID trap is named where it is made.
        self.assertIn('Secret ID', arch)

    def test_the_name_is_the_label_not_the_code(self):
        """`_rec_name = 'provider'` showed the raw selection value everywhere a
        record shows its name — the setup checklist read "outlook"."""
        row = self.Provider.create({'provider': 'outlook'})
        self.assertEqual(row.display_name, 'Microsoft 365')
        row.provider = 'imap'
        self.assertEqual(row.display_name, 'IMAP / SMTP')

    def test_oauth_providers_get_a_redirect_uri(self):
        row = self.Provider.create({'provider': 'outlook'})
        self.assertTrue(row.uses_oauth)
        self.assertTrue(row.redirect_uri)
        self.assertIn('/microsoft_oauth/callback', row.redirect_uri)

    @mute_logger('odoo.sql_db')
    def test_the_database_refuses_a_duplicate_too(self):
        """The uniqueness was declared with `_sql_constraints`, which Odoo 19
        ignores with nothing but a warning — so the table accepted two rows for
        one provider. The Python check above refuses a second row of any kind;
        this asserts the database would too, since that is the one an import
        or a raw INSERT still meets."""
        self.Provider.create({'provider': 'outlook', 'client_id': 'first'})

        with self.assertRaises(IntegrityError):
            self.env.cr.execute("""
                INSERT INTO pan_mail_provider (provider, client_id)
                VALUES ('outlook', 'second')
            """)
