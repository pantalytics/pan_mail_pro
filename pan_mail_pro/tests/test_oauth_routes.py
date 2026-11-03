# -*- coding: utf-8 -*-
"""The five routes this module opens, and the one that hands over credentials.

The OAuth callback is where a consent grant turns into a stored refresh token
and a mailbox. It had no test of any kind: `tests/test_connect_banner.py`
followed the button as far as the provider's consent screen and stopped there,
which is the half that cannot leak anything.

Two things are guarded here. The route surface, so a new route cannot land
`auth='public'` without somebody saying so out loud. And the callback itself,
whose failure modes are all silent by design: it renders a page either way, so
"it worked" and "it refused" look the same from the outside unless the test
looks at what was written.
"""
import ast
import os
from unittest.mock import patch
from urllib.parse import quote

import odoo
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.pan_mail_pro.models.res_users import (
    INBOX_URL,
    SETTINGS_URL,
    SIGN_IN_BACK_KEY,
    SIGN_IN_NOTICE_KEY,
)

GRAPH = 'odoo.addons.pan_mail_pro.models.providers.microsoft.graph_client.MicrosoftGraphClient'
CONTROLLER = 'odoo.addons.pan_mail_pro.controllers.main.MailProOAuthController'

CONTROLLERS = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'controllers')

# Every route this module declares, with the authentication it is allowed to
# use. Adding a row here is the moment to ask who may call the thing.
DECLARED_ROUTES = {
    '/mail_pro/connect': 'user',
    '/microsoft_oauth/callback': 'user',
    '/google_oauth/callback': 'user',
    '/mail_pro/pantalytics/return': 'user',
    '/mail_pro/microsoft/return': 'user',
}


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestRouteSurface(TransactionCase):
    """What this module exposes over HTTP, listed on purpose.

    Read from the source rather than from the registry: a decorator is a fact
    about the file, and the file is what a reviewer looks at. It also means
    this keeps working when Odoo moves its routing internals, which it does.
    """

    def _routes(self):
        found = {}
        for name in sorted(os.listdir(CONTROLLERS)):
            if not name.endswith('.py'):
                continue
            with open(os.path.join(CONTROLLERS, name), encoding='utf-8') as handle:
                tree = ast.parse(handle.read())
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for decorator in node.decorator_list:
                    if not isinstance(decorator, ast.Call):
                        continue
                    attribute = decorator.func
                    if getattr(attribute, 'attr', None) != 'route':
                        continue
                    auth = 'none'
                    for keyword in decorator.keywords:
                        if keyword.arg == 'auth':
                            auth = ast.literal_eval(keyword.value)
                    for argument in decorator.args:
                        for route in ([ast.literal_eval(argument)]
                                      if isinstance(argument, ast.Constant)
                                      else ast.literal_eval(argument)):
                            found[route] = auth
        return found

    def test_every_route_is_declared_here(self):
        """A new route arrives with a decision about who may call it.

        The list above is the decision. A route that is not on it fails this
        test rather than shipping with whatever `auth` was copied from its
        neighbour.
        """
        self.assertEqual(self._routes(), DECLARED_ROUTES)

    def test_nothing_is_public(self):
        """Every one of them is behind a login.

        The callback writes credentials, the connect route reads the user's
        provider, and the return route takes an approval key. None of that has
        an anonymous version.
        """
        public = [route for route, auth in self._routes().items()
                  if auth != 'user']
        self.assertFalse(public, 'routes reachable without a login: %s' % public)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestOAuthCallback(HttpCase):
    """The route that turns a consent grant into a mailbox."""

    def setUp(self):
        super().setUp()
        self.env['pan.mail.domain'].set_domains(['company.test'])
        self.env['pan.mail.provider'].create({
            'provider': 'outlook',
            'client_id': 'id',
            'client_secret': 'secret',
            'tenant_id': '11111111-2222-3333-4444-555555555555',
        })
        self.user = self.env['res.users'].create({
            'name': 'Nora Employee',
            'login': 'nora@company.test',
            'password': 'nora@company.test',
            'email': 'nora@company.test',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        self.authenticate('nora@company.test', 'nora@company.test')

    def _arm_state(self, state='nonce-123'):
        self.user.sudo().x_pan_mail_oauth_state = state
        return state

    def _callback(self, **params):
        query = '&'.join(f'{key}={value}' for key, value in params.items())
        return self.url_open(f'/microsoft_oauth/callback?{query}', allow_redirects=False)

    def _stored(self):
        return odoo.http.root.session_store.get(self.session.sid)

    def _started_from(self, back):
        """A sign-in in flight that started on `back`, as
        `res.users._pan_mail_remember_back` leaves it."""
        self.session[SIGN_IN_BACK_KEY] = back
        odoo.http.root.session_store.save(self.session)

    def _accounts(self):
        return self.env['pan.mail.account'].sudo().search(
            [('user_id', '=', self.user.id)])

    def _ledger(self):
        """What the callback wrote to the error ledger (tests/ledger.py keeps
        the rows in this transaction)."""
        return self.env['pan.mail.error'].sudo().search(
            [('code', '=', 'oauth.callback_failed')])

    def _grant(self, email, tokens=None):
        """The provider's side of a consent: a code that exchanges, and a /me
        that names `email` -- or, when it is None, a /me that failed, which
        every client reports as no address rather than by raising."""
        tokens = tokens or {'access_token': 'at', 'refresh_token': 'rt',
                            'token_expiry': '2030-01-01 00:00:00'}
        return (patch(f'{GRAPH}._exchange_code_for_tokens', return_value=tokens),
                patch(f'{GRAPH}.read_user_info', return_value={'email': email, 'name': None}))

    # ------------------------------------------------------------------ happy

    def test_a_grant_becomes_an_account_and_a_mailbox(self):
        self._arm_state()
        tokens = {'access_token': 'at', 'refresh_token': 'rt',
                  'token_expiry': '2030-01-01 00:00:00'}
        with patch(f'{GRAPH}._exchange_code_for_tokens', return_value=tokens), \
             patch(f'{GRAPH}.read_user_info', return_value={'email': 'nora@company.test', 'name': None}):
            response = self._callback(code='authcode', state='nonce-123')

        self.assertEqual(response.status_code, 303)
        self.assertEqual(self._stored()[SIGN_IN_NOTICE_KEY], {
            'type': 'success', 'message': 'Signed in as nora@company.test'})

        account = self._accounts()
        self.assertEqual(len(account), 1)
        self.assertEqual(account.email, 'nora@company.test')

        mailbox = self.env['pan.mail.mailbox'].sudo().search(
            [('email', '=ilike', 'nora@company.test')])
        self.assertEqual(len(mailbox), 1, 'one click, one mailbox')
        self.assertEqual(mailbox.owner_user_id, self.user)
        self.assertEqual(self.user.sudo().x_default_mailbox_id, mailbox)

    def test_a_user_with_no_email_gets_the_signed_in_address(self):
        """A fresh Odoo.sh admin has no email on the user (3 Nov); the
        address the provider vouched for fills it."""
        self.user.sudo().email = False
        self._arm_state()
        exchange, me = self._grant('nora@company.test')
        with exchange, me:
            self._callback(code='authcode', state='nonce-123')
        self.user.invalidate_recordset()
        self.assertEqual(self.user.email, 'nora@company.test')

    def test_an_email_already_there_stays(self):
        """Different from the sign-in, and still the user's: "Signed in as"
        shows the difference, nothing here enforces one."""
        self.user.sudo().email = 'nora.private@elsewhere.test'
        self._arm_state()
        exchange, me = self._grant('nora@company.test')
        with exchange, me:
            response = self._callback(code='authcode', state='nonce-123')
        self.assertEqual(response.status_code, 303)
        self.user.invalidate_recordset()
        self.assertEqual(self.user.email, 'nora.private@elsewhere.test')
        self.assertEqual(self._accounts().email, 'nora@company.test')

    def test_a_mailbox_that_failed_for_want_of_a_token_gets_another_go(self):
        mailbox = self.env['pan.mail.mailbox'].sudo().create({
            'email': 'nora@company.test',
            'provider': 'outlook',
            'owner_user_id': self.user.id,
        })
        mailbox.write({'state': 'error', 'error_message': 'No token'})

        self._arm_state()
        tokens = {'access_token': 'at', 'refresh_token': 'rt',
                  'token_expiry': '2030-01-01 00:00:00'}
        with patch(f'{GRAPH}._exchange_code_for_tokens', return_value=tokens), \
             patch(f'{GRAPH}.read_user_info', return_value={'email': 'nora@company.test', 'name': None}):
            self._callback(code='authcode', state='nonce-123')

        mailbox.invalidate_recordset()
        self.assertEqual(mailbox.state, 'draft')
        self.assertFalse(mailbox.error_message)

    # ----------------------------------------------------------- where it lands

    def test_a_sign_in_that_works_lands_where_it_started(self):
        """No page in between (#370): straight back to the screen the button
        was on, which says who signed in, once."""
        self._started_from('/odoo/action-42/7?view_type=form')
        self._arm_state()
        exchange, me = self._grant('nora@company.test')
        with exchange, me:
            response = self._callback(code='authcode', state='nonce-123')
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers['Location'].endswith(
            '/odoo/action-42/7?view_type=form'), response.headers['Location'])
        self.assertNotIn(SIGN_IN_BACK_KEY, self._stored(), 'good once')

        info = self.make_jsonrpc_request('/web/session/get_session_info', {})
        self.assertEqual(info['pan_mail_notice']['message'], 'Signed in as nora@company.test')
        again = self.make_jsonrpc_request('/web/session/get_session_info', {})
        self.assertFalse(again['pan_mail_notice'], 'shown on one page load, not every one')

    def test_a_failed_sign_in_links_back_where_it_started(self):
        self._started_from('/odoo/action-42/7')
        self._arm_state()
        response = self._callback(error='access_denied', error_description='User+said+no',
                                  state='nonce-123')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Connection Failed', response.text)
        self.assertIn('href="/odoo/action-42/7"', response.text)
        self.assertNotIn('href="/odoo"', response.text)

    def test_a_back_on_another_host_is_refused(self):
        exchange, me = self._grant('nora@company.test')
        for back in ('https://evil.test/odoo', '//evil.test/odoo', '/\\evil.test/odoo',
                     'odoo/settings'):
            with self.subTest(back=back), exchange, me:
                self._started_from(back)
                self._arm_state()
                response = self._callback(code='authcode', state='nonce-123')
                self.assertEqual(response.status_code, 303)
                self.assertTrue(response.headers['Location'].endswith(INBOX_URL),
                                response.headers['Location'])
                self.assertNotIn('evil.test', response.headers['Location'])

    def test_no_known_start_lands_an_administrator_on_settings(self):
        self.authenticate('admin', 'admin')
        self.env.ref('base.user_admin').sudo().x_pan_mail_oauth_state = 'nonce-admin'
        exchange, me = self._grant('admin@company.test')
        with exchange, me:
            response = self._callback(code='authcode', state='nonce-admin')
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers['Location'].endswith(SETTINGS_URL),
                        response.headers['Location'])

    def test_the_banner_s_start_is_kept(self):
        """`/mail_pro/connect?back=` from the banner, and the same tab."""
        back = '/odoo/action-pan_mail_pro.action_pan_mail_conversation?mailbox=3'
        response = self.url_open(f'/mail_pro/connect?back={quote(back, safe="")}',
                                 allow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn('login.microsoftonline.com', response.headers['Location'])
        self.assertEqual(self._stored()[SIGN_IN_BACK_KEY], back)

    def test_a_button_s_start_is_the_screen_it_was_pressed_on(self):
        """A button is an RPC: its Referer is the screen. Settings loses its
        tab in a Referer, so it is named in full."""
        for screen, kept in (('/odoo/action-42/7', '/odoo/action-42/7'),
                             ('/odoo/settings', SETTINGS_URL)):
            with self.subTest(screen=screen):
                action = self.make_jsonrpc_request(
                    '/web/dataset/call_button/res.users/action_connect_mailbox', {
                        'model': 'res.users', 'method': 'action_connect_mailbox',
                        'args': [[self.user.id]], 'kwargs': {}},
                    headers={'Referer': self.base_url() + screen})
                self.assertEqual(action['target'], 'self', 'same tab, so it can come back')
                self.assertEqual(self._stored()[SIGN_IN_BACK_KEY], kept)

    # ----------------------------------------------------------------- refusal

    def test_the_provider_s_own_error_stores_nothing(self):
        self._arm_state()
        response = self._callback(error='access_denied',
                                  error_description='User+said+no',
                                  state='nonce-123')

        self.assertIn('Connection Failed', response.text)
        self.assertFalse(self._accounts())

    def test_a_replayed_state_is_refused(self):
        """The nonce is good once. A callback URL in somebody's history, or
        pasted from a log, is not a second grant."""
        self._arm_state()
        tokens = {'access_token': 'at', 'refresh_token': 'rt',
                  'token_expiry': '2030-01-01 00:00:00'}
        with patch(f'{GRAPH}._exchange_code_for_tokens', return_value=tokens), \
             patch(f'{GRAPH}.read_user_info', return_value={'email': 'nora@company.test', 'name': None}):
            self._callback(code='authcode', state='nonce-123')
            replay = self._callback(code='authcode', state='nonce-123')

        self.assertIn('Security validation failed', replay.text)

    def test_a_foreign_state_is_refused(self):
        self._arm_state()
        response = self._callback(code='authcode', state='somebody-elses-nonce')

        self.assertIn('Security validation failed', response.text)
        self.assertFalse(self._accounts())

    def test_no_state_at_all_is_refused(self):
        self._arm_state()
        response = self._callback(code='authcode')

        self.assertIn('Security validation failed', response.text)
        self.assertFalse(self._accounts())

    def test_a_grant_with_no_code_stores_nothing(self):
        self._arm_state()
        response = self._callback(state='nonce-123')

        self.assertIn('Connection Failed', response.text)
        self.assertFalse(self._accounts())

    def test_a_failed_exchange_says_so_and_stores_nothing(self):
        """The provider accepted the redirect and then refused the code. The
        page has to say that rather than claiming a connection."""
        self._arm_state()
        with patch(f'{GRAPH}._exchange_code_for_tokens',
                   side_effect=Exception('invalid_grant')):
            response = self._callback(code='expired', state='nonce-123')

        self.assertIn('Connection Failed', response.text)
        self.assertFalse(self._accounts())

    def test_a_grant_that_names_no_address_stores_nothing(self):
        """The guard that keeps B's tokens off A's row compares addresses,
        so a consent with no address is no consent: nothing stored, a page
        that says so, and a row in the ledger."""
        self._arm_state()
        exchange, me = self._grant(None)
        with exchange, me:
            response = self._callback(code='authcode', state='nonce-123')

        self.assertIn('Connection Failed', response.text)
        self.assertIn('did not say which address', response.text)
        self.assertFalse(self._accounts())
        row = self._ledger()
        self.assertEqual(len(row), 1)
        self.assertEqual(row.level, 'error')
        self.assertIn('did not say which address', row.message)

    def test_a_blank_address_never_lands_on_another_identity_s_tokens(self):
        """Connected as A, then a second consent whose /me fails. The tokens
        in hand may be B's, and with no address to compare, A's row used to
        take them unchallenged. A keeps what A had."""
        self._arm_state()
        exchange, me = self._grant('nora@company.test', tokens={
            'access_token': 'at-a', 'refresh_token': 'rt-a',
            'token_expiry': '2030-01-01 00:00:00'})
        with exchange, me:
            self._callback(code='authcode', state='nonce-123')

        self._arm_state('nonce-456')
        exchange, me = self._grant(None, tokens={
            'access_token': 'at-b', 'refresh_token': 'rt-b',
            'token_expiry': '2030-01-01 00:00:00'})
        with exchange, me:
            response = self._callback(code='authcode', state='nonce-456')

        self.assertIn('Connection Failed', response.text)
        account = self._accounts()
        account.invalidate_recordset()
        self.assertEqual(len(account), 1)
        self.assertEqual(account.email, 'nora@company.test')
        self.assertEqual(account.access_token, 'at-a')
        self.assertEqual(account.refresh_token, 'rt-a')

    def test_a_mailbox_that_cannot_be_claimed_is_said_and_in_the_ledger(self):
        """The credentials are the point and the mailbox the convenience: a
        claim the mailbox constraint refuses keeps the account, tells the
        person a mailbox is missing rather than claiming one, and leaves the
        reason in the ledger as a warning, where an administrator finds it."""
        self._arm_state()
        exchange, me = self._grant('nora@company.test')
        with exchange, me, patch(f'{CONTROLLER}._claim_personal_mailbox',
                                 side_effect=ValueError('the constraint said no')):
            response = self._callback(code='authcode', state='nonce-123')

        self.assertEqual(response.status_code, 303, 'signed in all the same')
        notice = self._stored()[SIGN_IN_NOTICE_KEY]
        self.assertEqual(notice['type'], 'warning')
        self.assertIn('Your mailbox is not set up yet', notice['message'])
        self.assertEqual(len(self._accounts()), 1)
        self.assertFalse(self.env['pan.mail.mailbox'].sudo().with_context(
            active_test=False).search([('owner_user_id', '=', self.user.id)]))
        row = self._ledger()
        self.assertEqual(len(row), 1)
        self.assertEqual(row.level, 'warning')
        self.assertIn('the constraint said no', row.message)

    def test_a_grant_before_the_domains_are_set_connects_the_account_and_no_mailbox(self):
        """Users are invited before setup is done, on purpose. The consent
        stores the credentials; the mailbox waits for the domain list, and the
        refusal of the mailbox must not read as a failed connection or leave
        a row past the gate."""
        self.env['pan.mail.domain'].sudo().search([]).unlink()
        self._arm_state()
        tokens = {'access_token': 'at', 'refresh_token': 'rt',
                  'token_expiry': '2030-01-01 00:00:00'}
        with patch(f'{GRAPH}._exchange_code_for_tokens', return_value=tokens), \
             patch(f'{GRAPH}.read_user_info', return_value={'email': 'nora@company.test', 'name': None}):
            response = self._callback(code='authcode', state='nonce-123')
        self.assertEqual(response.status_code, 303)
        self.assertEqual(len(self._accounts()), 1)
        self.assertFalse(self.env['pan.mail.mailbox'].sudo().with_context(
            active_test=False).search([('owner_user_id', '=', self.user.id)]))

    def test_a_portal_user_is_refused_at_the_callback(self):
        """A customer with a portal login and the callback URL: no account."""
        portal = self.env['res.users'].create({
            'name': 'Customer',
            'login': 'customer@example.test',
            'password': 'customer@example.test',
            'group_ids': [(6, 0, [self.env.ref('base.group_portal').id])],
        })
        portal.sudo().x_pan_mail_oauth_state = 'nonce-portal'
        self.authenticate('customer@example.test', 'customer@example.test')
        tokens = {'access_token': 'at', 'refresh_token': 'rt',
                  'token_expiry': '2030-01-01 00:00:00'}
        with patch(f'{GRAPH}._exchange_code_for_tokens', return_value=tokens), \
             patch(f'{GRAPH}.read_user_info', return_value={'email': 'customer@example.test', 'name': None}):
            response = self._callback(code='authcode', state='nonce-portal')
        self.assertIn('Only internal users', response.text)
        self.assertFalse(self.env['pan.mail.account'].sudo().search(
            [('user_id', '=', portal.id)]))

    def test_a_shared_mailbox_is_never_repurposed(self):
        """Somebody configured `info@` on purpose. A personal grant for that
        address connects the account and leaves the mailbox alone."""
        shared = self.env['pan.mail.mailbox'].sudo().create({
            'email': 'info@company.test',
            'provider': 'outlook',
        })
        self._arm_state()
        tokens = {'access_token': 'at', 'refresh_token': 'rt',
                  'token_expiry': '2030-01-01 00:00:00'}
        with patch(f'{GRAPH}._exchange_code_for_tokens', return_value=tokens), \
             patch(f'{GRAPH}.read_user_info', return_value={'email': 'info@company.test', 'name': None}):
            self._callback(code='authcode', state='nonce-123')

        shared.invalidate_recordset()
        self.assertFalse(shared.owner_user_id, 'still shared')
        self.assertTrue(self._accounts(), 'but the account was stored')
