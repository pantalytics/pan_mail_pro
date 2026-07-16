# -*- coding: utf-8 -*-
from psycopg2 import IntegrityError

from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged('pan_outlook_pro', 'post_install', '-at_install')
class TestMailAccount(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Account = cls.env['pan.mail.account']
        cls.user = cls.env['res.users'].create({
            'name': 'Account Owner',
            'login': 'account_owner@test.local',
            'email': 'account_owner@test.local',
        })

    def test_tokens_round_trip_through_encryption(self):
        account = self.Account.create({
            'email': 'user@test.local',
            'provider': 'microsoft',
            'user_id': self.user.id,
            'refresh_token': 'secret-refresh',
            'access_token': 'secret-access',
        })
        account.invalidate_recordset()

        self.assertEqual(account.refresh_token, 'secret-refresh')
        self.assertEqual(account.access_token, 'secret-access')

    def test_tokens_are_not_stored_in_plain_text(self):
        account = self.Account.create({
            'email': 'user@test.local',
            'provider': 'microsoft',
            'user_id': self.user.id,
            'refresh_token': 'secret-refresh',
        })
        account.invalidate_recordset()

        self.assertNotEqual(account.refresh_token_encrypted, 'secret-refresh')
        self.assertNotIn('secret-refresh', account.refresh_token_encrypted or '')

    def test_connected_follows_the_refresh_token(self):
        """A refresh token is what makes an account outlive the next hour."""
        account = self.Account.create({
            'email': 'user@test.local', 'provider': 'microsoft', 'user_id': self.user.id,
        })
        self.assertFalse(account.connected)

        account.refresh_token = 'secret-refresh'
        self.assertTrue(account.connected)

    def test_service_account_needs_no_user(self):
        """A Gmail shared mailbox has credentials but no Odoo user behind it."""
        account = self.Account.create({'email': 'sales@company.test', 'provider': 'microsoft'})

        self.assertTrue(account)
        self.assertFalse(account.user_id)

    def test_many_service_accounts_can_coexist(self):
        """UNIQUE(user_id, provider) must not collapse service accounts together.

        Postgres treats NULLs as distinct in a unique index, which is exactly
        what makes this work. Anyone "fixing" the constraint with COALESCE would
        silently limit the whole database to one service account per provider.
        """
        self.Account.create({'email': 'sales@company.test', 'provider': 'microsoft'})
        self.Account.create({'email': 'support@company.test', 'provider': 'microsoft'})
        self.env.cr.flush()

        service_accounts = self.Account.search([
            ('user_id', '=', False), ('provider', '=', 'microsoft'),
        ])
        self.assertEqual(len(service_accounts), 2)

    @mute_logger('odoo.sql_db')
    def test_user_cannot_have_two_accounts_on_one_provider(self):
        self.Account.create({
            'email': 'user@test.local', 'provider': 'microsoft', 'user_id': self.user.id,
        })
        with self.assertRaises(IntegrityError):
            self.Account.create({
                'email': 'other@test.local', 'provider': 'microsoft', 'user_id': self.user.id,
            })
            self.env.cr.flush()
