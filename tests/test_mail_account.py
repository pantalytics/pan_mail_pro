# -*- coding: utf-8 -*-
from psycopg2 import IntegrityError

from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged('pan_mail_pro', 'post_install', '-at_install')
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
            'provider': 'outlook',
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
            'provider': 'outlook',
            'user_id': self.user.id,
            'refresh_token': 'secret-refresh',
        })
        account.invalidate_recordset()

        self.assertNotEqual(account.refresh_token_encrypted, 'secret-refresh')
        self.assertNotIn('secret-refresh', account.refresh_token_encrypted or '')

    def test_connected_follows_the_refresh_token(self):
        """A refresh token is what makes an account outlive the next hour."""
        account = self.Account.create({
            'email': 'user@test.local', 'provider': 'outlook', 'user_id': self.user.id,
        })
        self.assertFalse(account.connected)

        account.refresh_token = 'secret-refresh'
        self.assertTrue(account.connected)

    def test_service_account_needs_no_user(self):
        """A Gmail shared mailbox has credentials but no Odoo user behind it."""
        account = self.Account.create({'email': 'sales@company.test', 'provider': 'outlook'})

        self.assertTrue(account)
        self.assertFalse(account.user_id)

    def test_many_service_accounts_can_coexist(self):
        """UNIQUE(user_id, provider) must not collapse service accounts together.

        Postgres treats NULLs as distinct in a unique index, which is exactly
        what makes this work. Anyone "fixing" the constraint with COALESCE would
        silently limit the whole database to one service account per provider.
        """
        self.Account.create({'email': 'sales@company.test', 'provider': 'outlook'})
        self.Account.create({'email': 'support@company.test', 'provider': 'outlook'})
        self.env.cr.flush()

        service_accounts = self.Account.search([
            ('user_id', '=', False), ('provider', '=', 'outlook'),
        ])
        self.assertEqual(len(service_accounts), 2)

    def test_the_oauth_callback_lands_on_an_account(self):
        """_store_tokens is the one write path a consent screen comes back to."""
        user = self.env['res.users'].create({
            'name': 'Fresh Connection', 'login': 'fresh@test.local', 'email': 'fresh@test.local',
        })

        self.Account._store_tokens(
            'outlook', user, 'fresh@test.local', 'new-access', 'new-refresh', False)

        account = self.Account.search([('user_id', '=', user.id), ('provider', '=', 'outlook')])
        self.assertEqual(len(account), 1)
        self.assertEqual(account.email, 'fresh@test.local')
        self.assertEqual(account.refresh_token, 'new-refresh')

    def test_a_reauthorization_keeps_a_refresh_token_it_was_not_given(self):
        """Google issues a refresh token once and never again.

        Overwriting the stored one with the empty value of a later consent is
        how an account silently stops working an hour after re-authorizing.
        """
        account = self.Account.create({
            'email': 'again@test.local', 'provider': 'gmail', 'user_id': self.user.id,
            'refresh_token': 'the-only-one', 'access_token': 'old-access',
        })

        self.Account._store_tokens(
            'gmail', self.user, 'again@test.local', 'fresh-access', None, False)
        account.invalidate_recordset()

        self.assertEqual(account.refresh_token, 'the-only-one')
        self.assertEqual(account.access_token, 'fresh-access')

    def test_stored_connection_flag_follows_the_account(self):
        """x_pan_mail_connected is stored and drives the mailbox owner domains.

        If the depends chain to the account breaks, this field stops updating
        and the dropdown quietly empties out.
        """
        self.assertFalse(self.user.x_pan_mail_connected)

        account = self.Account.create({
            'email': 'toggle@test.local', 'provider': 'outlook', 'user_id': self.user.id,
            'refresh_token': 'some-refresh',
        })
        self.assertTrue(self.user.x_pan_mail_connected)

        account.refresh_token = False
        self.assertFalse(self.user.x_pan_mail_connected)

    def test_disconnect_clears_the_account(self):
        self.Account.create({
            'email': 'bye@test.local', 'provider': 'outlook', 'user_id': self.user.id,
            'access_token': 'a', 'refresh_token': 'r',
        })

        self.user.action_disconnect_mailbox('outlook')

        account = self.Account.search([('user_id', '=', self.user.id)])
        self.assertFalse(account.refresh_token_encrypted)
        self.assertFalse(account.access_token_encrypted)
        self.assertFalse(self.user.x_pan_mail_connected)

    def test_disconnecting_one_provider_keeps_the_other(self):
        """Two providers, one user: revoking one is not revoking both."""
        self.Account.create({
            'email': 'both@test.local', 'provider': 'outlook', 'user_id': self.user.id,
            'refresh_token': 'ms',
        })
        self.Account.create({
            'email': 'both@gmail.test', 'provider': 'gmail', 'user_id': self.user.id,
            'refresh_token': 'goog',
        })

        self.user.action_disconnect_mailbox('outlook')

        self.assertTrue(self.user.x_pan_mail_connected)
        self.assertTrue(self.Account._for_user(self.user, 'gmail').connected)

    def test_archived_user_keeps_readable_credentials(self):
        """Archived accounts stay reachable from the user.

        The One2many carries active_test=False for exactly this: otherwise a
        stored recompute would report an archived user as disconnected and the
        difference would only show up in a mailbox that stopped sending.
        """
        account = self.Account.create({
            'email': 'archived@test.local', 'provider': 'outlook', 'user_id': self.user.id,
            'refresh_token': 'still-valid',
        })
        account.active = False
        self.user.invalidate_recordset()

        self.assertIn(account, self.user.x_pan_mail_account_ids)
        self.assertTrue(self.user.x_pan_mail_connected)

    @mute_logger('odoo.sql_db')
    def test_user_cannot_have_two_accounts_on_one_provider(self):
        self.Account.create({
            'email': 'user@test.local', 'provider': 'outlook', 'user_id': self.user.id,
        })
        with self.assertRaises(IntegrityError):
            self.Account.create({
                'email': 'other@test.local', 'provider': 'outlook', 'user_id': self.user.id,
            })
            self.env.cr.flush()
