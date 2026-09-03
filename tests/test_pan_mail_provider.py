# -*- coding: utf-8 -*-
"""Guards `pan.mail.provider`: which credentials count as complete per
provider, that switching keeps what you switch away from, and that a secret
is never handed back once it is saved.
"""
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


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

    def test_only_one_provider_in_use(self):
        self.Provider.create({'provider': 'outlook', 'in_use': True})
        with self.assertRaises(ValidationError):
            self.Provider.create({'provider': 'gmail', 'in_use': True})

    def test_switching_in_use_keeps_the_old_credentials(self):
        """Switching providers must not lose the one you switch away from —
        the whole point of a row per provider instead of one global choice."""
        outlook = self.Provider.create({
            'provider': 'outlook', 'in_use': True,
            'client_id': 'id', 'client_secret': 'secret', 'tenant_id': 'tenant',
        })
        gmail = self.Provider.create({
            'provider': 'gmail', 'client_id': 'gid', 'client_secret': 'gsecret',
        })
        outlook.in_use = False
        gmail.in_use = True

        self.assertEqual(outlook.client_id, 'id')
        self.assertTrue(outlook.credentials_set)

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

    def test_oauth_providers_get_a_redirect_uri(self):
        row = self.Provider.create({'provider': 'outlook'})
        self.assertTrue(row.uses_oauth)
        self.assertTrue(row.redirect_uri)
        self.assertIn('/microsoft_oauth/callback', row.redirect_uri)
