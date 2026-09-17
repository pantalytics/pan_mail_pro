# -*- coding: utf-8 -*-
"""The four routes this module opens, and the one that hands over credentials.

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

from odoo.tests import HttpCase, TransactionCase, tagged

GRAPH = 'odoo.addons.pan_mail_pro.models.providers.microsoft.graph_client.MicrosoftGraphClient'

CONTROLLERS = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'controllers')

# Every route this module declares, with the authentication it is allowed to
# use. Adding a row here is the moment to ask who may call the thing.
DECLARED_ROUTES = {
    '/mail_pro/connect': 'user',
    '/microsoft_oauth/callback': 'user',
    '/google_oauth/callback': 'user',
    '/mail_pro/pantalytics/return': 'user',
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
            'tenant_id': 'tenant',
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
        return self.url_open(f'/microsoft_oauth/callback?{query}')

    def _accounts(self):
        return self.env['pan.mail.account'].sudo().search(
            [('user_id', '=', self.user.id)])

    # ------------------------------------------------------------------ happy

    def test_a_grant_becomes_an_account_and_a_mailbox(self):
        self._arm_state()
        tokens = {'access_token': 'at', 'refresh_token': 'rt',
                  'token_expiry': '2030-01-01 00:00:00'}
        with patch(f'{GRAPH}._exchange_code_for_tokens', return_value=tokens), \
             patch(f'{GRAPH}.get_user_email', return_value='nora@company.test'):
            response = self._callback(code='authcode', state='nonce-123')

        self.assertEqual(response.status_code, 200)
        self.assertIn('Mailbox Connected', response.text)

        account = self._accounts()
        self.assertEqual(len(account), 1)
        self.assertEqual(account.email, 'nora@company.test')

        mailbox = self.env['pan.mail.mailbox'].sudo().search(
            [('email', '=ilike', 'nora@company.test')])
        self.assertEqual(len(mailbox), 1, 'one click, one mailbox')
        self.assertEqual(mailbox.owner_user_id, self.user)
        self.assertEqual(self.user.sudo().x_default_mailbox_id, mailbox)

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
             patch(f'{GRAPH}.get_user_email', return_value='nora@company.test'):
            self._callback(code='authcode', state='nonce-123')

        mailbox.invalidate_recordset()
        self.assertEqual(mailbox.state, 'draft')
        self.assertFalse(mailbox.error_message)

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
             patch(f'{GRAPH}.get_user_email', return_value='nora@company.test'):
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
             patch(f'{GRAPH}.get_user_email', return_value='info@company.test'):
            self._callback(code='authcode', state='nonce-123')

        shared.invalidate_recordset()
        self.assertFalse(shared.owner_user_id, 'still shared')
        self.assertTrue(self._accounts(), 'but the account was stored')
