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

    def test_writing_a_token_on_a_user_creates_the_account(self):
        """The OAuth callback writes to res.users and must land on an account.

        This is what lets controllers/main.py and the token refresh stay
        untouched while the credentials move.
        """
        user = self.env['res.users'].create({
            'name': 'Fresh Connection', 'login': 'fresh@test.local', 'email': 'fresh@test.local',
        })

        user.sudo().write({
            'x_microsoft_access_token': 'new-access',
            'x_microsoft_refresh_token': 'new-refresh',
        })

        account = self.Account.search([('user_id', '=', user.id), ('provider', '=', 'microsoft')])
        self.assertEqual(len(account), 1)
        self.assertEqual(account.email, 'fresh@test.local')
        self.assertEqual(account.refresh_token, 'new-refresh')

    def test_clearing_tokens_does_not_create_an_empty_account(self):
        """Disconnecting a user who never connected must leave no trace.

        A blank account would show up as a connection that was never made.
        """
        user = self.env['res.users'].create({
            'name': 'Never Connected', 'login': 'never@test.local', 'email': 'never@test.local',
        })

        user.sudo().write({
            'x_microsoft_access_token_encrypted': False,
            'x_microsoft_refresh_token_encrypted': False,
            'x_microsoft_token_expiry': False,
        })

        self.assertFalse(self.Account.search([('user_id', '=', user.id)]))

    def test_user_reads_tokens_back_through_the_account(self):
        account = self.Account.create({
            'email': 'roundtrip@test.local', 'provider': 'microsoft', 'user_id': self.user.id,
            'access_token': 'account-access', 'refresh_token': 'account-refresh',
        })
        self.user.invalidate_recordset()

        self.assertEqual(self.user.x_microsoft_access_token, 'account-access')
        self.assertEqual(self.user.x_microsoft_refresh_token, 'account-refresh')
        self.assertEqual(
            self.user.x_microsoft_refresh_token_encrypted, account.refresh_token_encrypted)

    def test_stored_connection_flag_follows_the_account(self):
        """x_microsoft_oauth_connected is stored and drives view domains.

        If the depends chain to the account breaks, this field stops updating
        and the mailbox owner dropdown quietly empties out.
        """
        self.assertFalse(self.user.x_microsoft_oauth_connected)

        account = self.Account.create({
            'email': 'toggle@test.local', 'provider': 'microsoft', 'user_id': self.user.id,
            'refresh_token': 'some-refresh',
        })
        self.assertTrue(self.user.x_microsoft_oauth_connected)

        account.refresh_token = False
        self.assertFalse(self.user.x_microsoft_oauth_connected)

    def test_disconnect_clears_the_account(self):
        self.Account.create({
            'email': 'bye@test.local', 'provider': 'microsoft', 'user_id': self.user.id,
            'access_token': 'a', 'refresh_token': 'r',
        })

        self.user.action_disconnect_microsoft()

        account = self.Account.search([('user_id', '=', self.user.id)])
        self.assertFalse(account.refresh_token_encrypted)
        self.assertFalse(account.access_token_encrypted)
        self.assertFalse(self.user.x_microsoft_oauth_connected)

    def test_archived_user_keeps_readable_credentials(self):
        """Archived accounts stay visible through the proxy.

        The One2many carries active_test=False for exactly this: otherwise a
        stored recompute would report an archived user as disconnected and the
        difference would only show up in a mailbox that stopped sending.
        """
        account = self.Account.create({
            'email': 'archived@test.local', 'provider': 'microsoft', 'user_id': self.user.id,
            'refresh_token': 'still-valid',
        })
        account.active = False
        self.user.invalidate_recordset()

        self.assertEqual(self.user.x_microsoft_refresh_token, 'still-valid')

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
