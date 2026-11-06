# -*- coding: utf-8 -*-
"""A Gmail shared address signs in as itself, from its mailbox form.

On Google, sales@ is a Workspace user of its own: it sends and reads with its
own sign-in and nobody's in Odoo (`GoogleGmailClient._service_account`). Until
19.0.40.0.0 the only sign-in round stored its tokens on the Odoo user who
pressed the button, so the guide's "sign in as that address" could not be
done without disconnecting yourself first. These tests walk the round that
replaced it: the mailbox's button, the consent URL it opens, and what the
callback stores, and refuses to store.
"""
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from odoo.tests import HttpCase, tagged

from odoo.addons.pan_mail_pro.controllers.main import ADDRESS_SIGN_IN_KEY
from odoo.addons.pan_mail_pro.models.res_users import SIGN_IN_NOTICE_KEY
from .common import groups_field, session_store

GMAIL = 'odoo.addons.pan_mail_pro.models.providers.google.gmail_client.GoogleGmailClient'
TOKENS = {'access_token': 'at', 'refresh_token': 'rt',
          'token_expiry': '2030-01-01 00:00:00', 'scope': 'openid email gmail.modify'}


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestGmailAddressSignIn(HttpCase):

    def setUp(self):
        super().setUp()
        self.env['pan.mail.domain'].set_domains(['company.test'])
        self.env['pan.mail.provider'].create({
            'provider': 'gmail', 'client_id': 'cid.apps.googleusercontent.com',
            'client_secret': 'secret',
        })
        self.manager = self.env['res.users'].create({
            'name': 'Mia Manager', 'login': 'mia@company.test',
            'password': 'mia@company.test', 'email': 'mia@company.test',
            groups_field(self.env): [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('pan_mail_pro.group_mail_mailbox_manager').id])],
        })
        self.mailbox = self.env['pan.mail.mailbox'].create({
            'email': 'sales@company.test', 'provider': 'gmail', 'sync_level': 'replies',
        })
        self.authenticate('mia@company.test', 'mia@company.test')

    def _start(self):
        return self.url_open(
            f'/mail_pro/connect?as_address=1&mailbox_id={self.mailbox.id}',
            allow_redirects=False)

    def _callback(self, **params):
        query = '&'.join(f'{key}={value}' for key, value in params.items())
        return self.url_open(f'/google_oauth/callback?{query}', allow_redirects=False)

    def _state(self):
        return session_store().get(self.session.sid)[ADDRESS_SIGN_IN_KEY]['state']

    def _grant(self, email):
        return (patch(f'{GMAIL}._exchange_code_for_tokens', return_value=TOKENS),
                patch(f'{GMAIL}.read_user_info', return_value={
                    'email': email, 'provider_user_id': 'sub-1', 'tenant_id': 'company.test'}))

    def _address_accounts(self):
        return self.env['pan.mail.account'].sudo().with_context(active_test=False).search(
            [('provider', '=', 'gmail'), ('email', '=ilike', 'sales@company.test')])

    def test_the_mailbox_offers_the_sign_in_and_not_try_again(self):
        self.assertTrue(self.mailbox.signs_in_as_itself)
        self.assertIn('Sign in as this address', self.mailbox.status_message)
        action = self.mailbox.with_user(self.manager).action_sign_in_as_address()
        self.assertEqual(action['url'],
                         f'/mail_pro/connect?as_address=1&mailbox_id={self.mailbox.id}')

    def test_the_consent_screen_asks_for_the_address_and_an_account_choice(self):
        response = self._start()
        self.assertEqual(response.status_code, 303)
        query = parse_qs(urlparse(response.headers['Location']).query)
        self.assertEqual(query['login_hint'], ['sales@company.test'])
        # The browser is usually signed in as the manager: the chooser is
        # forced, and consent with it, or Google hands back no refresh token.
        self.assertEqual(query['prompt'], ['select_account consent'])
        self.assertEqual(query['state'], [self._state()])

    def test_the_address_s_sign_in_lands_on_its_own_account(self):
        self._start()
        exchange, me = self._grant('sales@company.test')
        with exchange, me:
            response = self._callback(code='c', state=self._state())
        self.assertEqual(response.status_code, 303)
        self.assertIn(f'/{self.mailbox.id}', response.headers['Location'])
        account = self._address_accounts()
        self.assertEqual(len(account), 1)
        self.assertFalse(account.user_id, 'the address, not the manager who pressed it')
        self.assertEqual(account.provider_user_id, 'sub-1')
        self.assertTrue(self.mailbox._has_working_credentials())
        self.assertFalse(self.env['pan.mail.account'].sudo().search(
            [('user_id', '=', self.manager.id)]), "the manager's own row is untouched")
        self.assertEqual(session_store().get(self.session.sid)[SIGN_IN_NOTICE_KEY]['message'],
                         'Signed in as sales@company.test')

    def test_the_manager_s_own_sign_in_is_refused_and_nothing_stored(self):
        self._start()
        exchange, me = self._grant('mia@company.test')
        with exchange, me:
            response = self._callback(code='c', state=self._state())
        self.assertEqual(response.status_code, 200)
        self.assertIn('Sign in as sales@company.test', response.text)
        self.assertFalse(self._address_accounts())
        self.assertFalse(self.env['pan.mail.account'].sudo().search(
            [('user_id', '=', self.manager.id)]))

    def test_a_second_sign_in_updates_the_same_account(self):
        for _round in range(2):
            self._start()
            exchange, me = self._grant('Sales@company.test')
            with exchange, me:
                self._callback(code='c', state=self._state())
        self.assertEqual(len(self._address_accounts()), 1)

    def test_a_replayed_state_stores_nothing(self):
        self._start()
        state = self._state()
        self.url_open('/mail_pro/connect?as_address=1&mailbox_id=0', allow_redirects=False)
        exchange, me = self._grant('sales@company.test')
        with exchange, me:
            self._callback(code='c', state=state + 'x')
        self.assertFalse(self._address_accounts())

    def test_a_plain_user_may_not_start_it(self):
        self.env['res.users'].create({
            'name': 'Pim Plain', 'login': 'pim@company.test',
            'password': 'pim@company.test', 'email': 'pim@company.test',
            groups_field(self.env): [(6, 0, [self.env.ref('base.group_user').id])],
        })
        self.authenticate('pim@company.test', 'pim@company.test')
        response = self._start()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(ADDRESS_SIGN_IN_KEY, session_store().get(self.session.sid))

    def test_a_personal_mailbox_does_not_offer_it(self):
        mine = self.env['pan.mail.mailbox'].create({
            'email': 'mia@company.test', 'provider': 'gmail',
            'owner_user_id': self.manager.id,
        })
        self.assertEqual(mine.mailbox_type, 'personal')
        self.assertFalse(mine.signs_in_as_itself)
