# -*- coding: utf-8 -*-
"""Moving a Microsoft 365 customer off their own Azure app to ours.

A refresh token is bound to the client id that issued it, so no token moves
silently: every person signs in once more, through the Pantalytics app. What
this file guards is that nothing stops in between. Each account remembers the
app its token came from (`pan.mail.account.registration`) and refreshes with
that one; the switch keeps the own app's credentials on the row for them;
the people still on it are nudged to reconnect; and the old credentials go
only when nobody uses them and an administrator says so (ARCHITECTURE.md
§9.20).

The platform is faked at `requests.post` in `pan_mail_license`, Microsoft at
the Graph client's own methods or at its `requests.post`.
"""
import importlib.util
import os
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import odoo
from odoo.exceptions import UserError
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.pan_mail_pro.models.mail_provider_client import (
    get_provider_client, oauth_redirect_uri,
)
from odoo.addons.pan_mail_pro.models.pan_mail_provider import MS_APP_SESSION_KEY
from odoo.addons.pan_mail_pro.tests.test_pantalytics_app import (
    APP_ID, GRAPH, GRAPH_POST, PLATFORM_POST, PLATFORM_REDIRECT, _response, _started,
    a_browser, connect_to_pantalytics,
)

OWN_TENANT = '11111111-2222-3333-4444-555555555555'
OTHER_TENANT = '99999999-8888-7777-6666-555555555555'
_MIGRATION = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                          'migrations', '19.0.32.0.0', 'pre-migrate.py')


def own_row(env):
    """A Microsoft row on the customer's own app, as 19.0.31.0.0 leaves it."""
    return env['pan.mail.provider'].create({
        'provider': 'outlook', 'client_id': 'own-id', 'client_secret': 'own-secret',
        'tenant_id': OWN_TENANT})


def make_user(env, login, admin=False):
    groups = [env.ref('base.group_user').id]
    if admin:
        groups.append(env.ref('base.group_system').id)
    return env['res.users'].create({
        'name': login.split('@')[0].title(), 'login': login, 'password': login,
        'email': login, 'group_ids': [(6, 0, groups)]})


def connected_account(env, user, **values):
    vals = {'email': user.login, 'provider': 'outlook', 'user_id': user.id,
            'refresh_token': 'rt-old', 'access_token': 'at-old',
            'tenant_id': OWN_TENANT}
    vals.update(values)
    return env['pan.mail.account'].sudo().create(vals)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestTheAccountKeepsItsApp(TransactionCase):
    """Refresh and every token use go through the app that issued the token."""

    def setUp(self):
        super().setUp()
        self.row = own_row(self.env)
        self.client = get_provider_client(self.env, 'outlook')
        self.nora = make_user(self.env, 'nora@company.test')
        self.mia = make_user(self.env, 'mia@company.test')
        self.posted = []

    def microsoft(self):
        def handler(url, data=None, timeout=None, **kwargs):
            self.posted.append({'url': url, 'data': dict(data or {})})
            return _response(200, {'access_token': 'at-new', 'refresh_token': 'rt-new',
                                   'expires_in': 3600})
        return patch(GRAPH_POST, side_effect=handler)

    def _switch(self):
        connect_to_pantalytics(self.env)
        self.row.write({'registration': 'pantalytics', 'pantalytics_client_id': APP_ID})

    def test_an_account_made_on_an_own_row_is_an_own_account(self):
        self.assertEqual(connected_account(self.env, self.nora).registration, 'own')

    def test_after_the_switch_an_own_account_refreshes_with_the_own_app(self):
        account = connected_account(self.env, self.nora)
        self._switch()
        with self.microsoft():
            self.assertEqual(self.client.refresh_access_token(account), 'at-new')
        post, = self.posted
        self.assertEqual(post['url'],
                         f'https://login.microsoftonline.com/{OWN_TENANT}/oauth2/v2.0/token')
        self.assertEqual(post['data']['client_id'], 'own-id')
        self.assertEqual(post['data']['client_secret'], 'own-secret')
        self.assertEqual(account.sudo().refresh_token, 'rt-new')
        self.assertEqual(account.registration, 'own', 'a refresh is not a reconnect')

    def test_after_the_switch_an_app_account_refreshes_without_a_secret(self):
        self._switch()
        account = connected_account(self.env, self.mia, registration='pantalytics')
        with self.microsoft():
            self.client.refresh_access_token(account)
        post, = self.posted
        self.assertEqual(post['url'],
                         'https://login.microsoftonline.com/organizations/oauth2/v2.0/token')
        self.assertEqual(post['data']['client_id'], APP_ID)
        self.assertNotIn('client_secret', post['data'])

    def test_an_expired_token_is_refreshed_by_its_own_app_on_the_way_to_a_call(self):
        account = connected_account(self.env, self.nora, token_expiry='2020-01-01 00:00:00')
        self._switch()
        with self.microsoft():
            self.assertEqual(self.client.get_valid_token(account), 'at-new')
        self.assertEqual(self.posted[0]['data']['client_id'], 'own-id')

    def test_an_app_account_on_a_row_switched_back_still_refreshes_through_the_app(self):
        """The radio still goes back to the own app, and the accounts the
        app signed in keep refreshing with it: the account decides."""
        self._switch()
        account = connected_account(self.env, self.mia, registration='pantalytics')
        self.row.registration = 'own'
        with self.microsoft():
            self.client.refresh_access_token(account)
        self.assertNotIn('client_secret', self.posted[0]['data'])

    def test_a_stale_own_round_is_redeemed_by_the_own_app(self):
        """A connect started before the switch and finished after it was
        issued to the own app, and only the own app can redeem it."""
        self._switch()
        with self.microsoft():
            self.client._exchange_code_for_tokens(
                'authcode', 'https://odoo.test/microsoft_oauth/callback', registration='own')
        post, = self.posted
        self.assertEqual(post['data']['client_secret'], 'own-secret')
        self.assertEqual(post['data']['client_id'], 'own-id')

    def test_the_same_tenant_is_welcome_through_the_app(self):
        connected_account(self.env, self.nora)
        self._switch()
        self.assertIsNone(self.client.identity_refusal(
            {'tenant_id': OWN_TENANT.upper()}, registration='pantalytics'))
        self.assertIn(OTHER_TENANT, self.client.identity_refusal(
            {'tenant_id': OTHER_TENANT}, registration='pantalytics'))

    def test_without_a_tenant_on_any_account_the_own_registration_names_it(self):
        """Grants from before 19.0.28 kept no tenant; the own app's
        Directory (tenant) ID is still on the row and says which it is."""
        connected_account(self.env, self.nora, tenant_id=False)
        self._switch()
        self.assertIsNone(self.client.identity_refusal(
            {'tenant_id': OWN_TENANT}, registration='pantalytics'))
        self.assertTrue(self.client.identity_refusal(
            {'tenant_id': OTHER_TENANT}, registration='pantalytics'))

    def test_a_domain_as_tenant_names_no_directory_to_compare_with(self):
        self.row.tenant_id = 'company.test'
        connected_account(self.env, self.nora, tenant_id=False)
        self._switch()
        self.assertIsNone(self.client.identity_refusal(
            {'tenant_id': OTHER_TENANT}, registration='pantalytics'))


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestTheSwitch(TransactionCase):
    """One click for the administrator, and nothing breaks."""

    def setUp(self):
        super().setUp()
        self.env['pan.mail.domain'].set_domains(['company.test'])
        self.row = own_row(self.env)
        self.admin = make_user(self.env, 'ada@company.test', admin=True)
        self.nora = make_user(self.env, 'nora@company.test')
        self.ada_account = connected_account(self.env, self.admin)
        self.nora_account = connected_account(self.env, self.nora)
        self.row = self.row.with_user(self.admin)

    def test_without_a_pantalytics_connection_the_switch_says_where_to_connect(self):
        with patch(PLATFORM_POST) as post, a_browser(self.env), \
                self.assertRaisesRegex(UserError, 'step 1'):
            self.row.action_switch_to_pantalytics()
        post.assert_not_called()
        self.assertEqual(self.row.registration, 'own')

    def test_the_switch_keeps_the_own_app_and_signs_the_administrator_in(self):
        connect_to_pantalytics(self.env)
        with patch(PLATFORM_POST, return_value=_started()), \
                a_browser(self.env(user=self.admin)) as session:
            action = self.row.action_switch_to_pantalytics()
        self.assertEqual(self.row.registration, 'pantalytics')
        self.assertEqual((self.row.client_id, self.row.tenant_id), ('own-id', OWN_TENANT))
        self.assertTrue(self.row.sudo().client_secret_encrypted,
                        'the accounts it signed in still refresh with it')
        self.assertEqual(action['target'], 'self')
        query = parse_qs(urlparse(action['url']).query)
        self.assertEqual(query['client_id'], [APP_ID])
        self.assertEqual(query['login_hint'], ['ada@company.test'],
                         'the reconnect opens on the address already connected')
        self.assertEqual(session[MS_APP_SESSION_KEY]['state'], 'platform-state-1')
        self.assertEqual(self.nora_account.registration, 'own', 'nobody else moved yet')

    def test_a_platform_that_cannot_start_the_round_undoes_the_switch(self):
        connect_to_pantalytics(self.env)
        # `assertRaises`, not `assertRaisesRegex`: only Odoo's own wraps the
        # call in a savepoint, which stands in for the request's rollback.
        with patch(PLATFORM_POST, return_value=_response(503, {'status': 'down'})), \
                a_browser(self.env(user=self.admin)), \
                self.assertRaises(UserError) as refusal:
            self.row.action_switch_to_pantalytics()
        self.assertIn('not available', str(refusal.exception))
        self.row.invalidate_recordset()
        self.assertEqual(self.row.registration, 'own')

    def test_the_settings_line_switches_too(self):
        connect_to_pantalytics(self.env)
        settings = self.env['res.config.settings'].with_user(self.admin).create({})
        self.assertTrue(settings.x_microsoft_switchable)
        with patch(PLATFORM_POST, return_value=_started()), \
                a_browser(self.env(user=self.admin)):
            action = settings.action_switch_microsoft_app()
        self.assertEqual(urlparse(action['url']).netloc, 'login.microsoftonline.com')
        settings = self.env['res.config.settings'].with_user(self.admin).create({})
        self.assertFalse(settings.x_microsoft_switchable)

    def test_only_an_own_microsoft_row_switches(self):
        connect_to_pantalytics(self.env)
        self.row.sudo().registration = 'pantalytics'
        with self.assertRaises(UserError):
            self.row.action_switch_to_pantalytics()

    def test_after_the_switch_existing_accounts_still_count_as_connected(self):
        """The step stays green, the phase stays on, nobody is disconnected."""
        connect_to_pantalytics(self.env)
        self.row.sudo().registration = 'pantalytics'
        self.assertTrue(self.nora_account.connected)
        self.assertTrue(self.nora.x_pan_mail_connected)
        self.assertTrue(self.row.credentials_set)
        self.assertEqual(self.row.status, 'connected')


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestTheNudgeAndTheCleanup(TransactionCase):
    """Who is asked to reconnect, how many are left, and the way out."""

    def setUp(self):
        super().setUp()
        self.env['pan.mail.domain'].set_domains(['company.test'])
        self.row = own_row(self.env)
        self.admin = make_user(self.env, 'ada@company.test', admin=True)
        self.nora = make_user(self.env, 'nora@company.test')
        self.mia = make_user(self.env, 'mia@company.test')
        self.nora_account = connected_account(self.env, self.nora)
        self.mia_account = connected_account(self.env, self.mia)
        self.link = connect_to_pantalytics(self.env)

    def _switch(self):
        self.row.registration = 'pantalytics'

    def _settings(self):
        return self.env['res.config.settings'].with_user(self.admin).create({})

    def test_nobody_is_asked_before_the_switch(self):
        self.assertFalse(self.nora._pan_mail_should_prompt_reconnect())

    def test_an_own_account_is_asked_after_the_switch(self):
        self._switch()
        self.assertTrue(self.nora._pan_mail_should_prompt_reconnect())
        self.assertFalse(self.nora._pan_mail_should_prompt_connect(),
                         'connected: the other banner stays away')

    def test_an_account_through_the_app_is_left_alone(self):
        self._switch()
        self.nora_account.registration = 'pantalytics'
        self.assertFalse(self.nora._pan_mail_should_prompt_reconnect())

    def test_a_disconnected_account_is_asked_to_connect_not_to_reconnect(self):
        self._switch()
        self.nora.action_disconnect_mailbox()
        self.assertFalse(self.nora._pan_mail_should_prompt_reconnect())

    def test_nobody_is_asked_while_this_odoo_cannot_start_a_sign_in(self):
        self._switch()
        self.link.status = 'revoked'
        self.assertFalse(self.nora._pan_mail_should_prompt_reconnect())

    def test_a_copy_asks_nobody(self):
        self._switch()
        with patch('odoo.addons.pan_mail_pro.models.res_users.database_is_neutralized',
                   return_value=True):
            self.assertFalse(self.nora._pan_mail_should_prompt_reconnect())

    def test_the_count_and_the_cleanup(self):
        self.assertEqual(self.row.own_account_count, 0, 'not moving yet')
        self.assertFalse(self._settings().x_microsoft_own_left)
        self._switch()
        self.row.invalidate_recordset()
        self.assertEqual(self.row.own_account_count, 2)
        settings = self._settings()
        self.assertIn('2 people still sign in through your own app registration',
                      settings.x_microsoft_own_left)
        self.assertFalse(settings.x_microsoft_own_removable)
        with self.assertRaisesRegex(UserError, 'still sign in'):
            self.row.with_user(self.admin).action_remove_own_registration()

        self.nora_account.registration = 'pantalytics'
        self.row.invalidate_recordset()
        self.assertEqual(self.row.own_account_count, 1)
        self.assertIn('1 person still signs in', self._settings().x_microsoft_own_left)

        self.mia_account.registration = 'pantalytics'
        self.row.invalidate_recordset()
        settings = self._settings()
        self.assertFalse(settings.x_microsoft_own_left)
        self.assertTrue(settings.x_microsoft_own_removable)
        self.assertTrue(self.row.client_id, 'offered, never done on its own')

        settings.action_remove_own_registration()
        self.row.invalidate_recordset()
        self.assertFalse(self.row.client_id)
        self.assertFalse(self.row.tenant_id)
        self.assertFalse(self.row.sudo().client_secret_encrypted)
        self.assertFalse(self.row.has_own_registration)
        self.assertFalse(self._settings().x_microsoft_own_removable)
        self.assertEqual(self.row.registration, 'pantalytics')

    def test_an_own_row_cannot_remove_the_app_it_signs_in_with(self):
        with self.assertRaises(UserError):
            self.row.with_user(self.admin).action_remove_own_registration()
        self.assertEqual(self.row.client_id, 'own-id')


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestTheMigration(TransactionCase):
    """migrations/19.0.32.0.0: every existing account keeps its app."""

    def setUp(self):
        super().setUp()
        spec = importlib.util.spec_from_file_location('pan_switch_19_0_32_0_0', _MIGRATION)
        self.migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.migration)
        self.nora = make_user(self.env, 'nora@company.test')
        self.mia = make_user(self.env, 'mia@company.test')

    def _as_before(self, accounts):
        """The column as an upgrade finds it: there, and empty."""
        self.env.flush_all()
        self.env.cr.execute('UPDATE pan_mail_account SET registration = NULL WHERE id IN %s',
                            [tuple(accounts.ids)])
        self.env.invalidate_all()

    def test_existing_microsoft_and_google_accounts_become_own(self):
        own_row(self.env)
        microsoft = connected_account(self.env, self.nora)
        google = connected_account(self.env, self.mia, provider='gmail', tenant_id=False)
        self._as_before(microsoft | google)
        self.migration.migrate(self.env.cr, '19.0.31.0.0')
        self.env.invalidate_all()
        self.assertEqual(microsoft.registration, 'own')
        self.assertEqual(google.registration, 'own')

    def test_a_database_that_already_signed_in_through_the_app_keeps_that(self):
        """A 19.0.31.0.0 database on the Pantalytics app: its accounts came
        through our app, since its row never had an app of its own."""
        self.env['pan.mail.provider'].create({'provider': 'outlook',
                                              'pantalytics_client_id': APP_ID})
        account = connected_account(self.env, self.nora)
        self._as_before(account)
        self.migration.migrate(self.env.cr, '19.0.31.0.0')
        self.env.invalidate_all()
        self.assertEqual(account.registration, 'pantalytics')

    def test_a_value_already_there_is_kept_and_a_fresh_install_is_left_alone(self):
        own_row(self.env)
        account = connected_account(self.env, self.nora, registration='pantalytics')
        self.migration.migrate(self.env.cr, '19.0.31.0.0')
        self.migration.migrate(self.env.cr, None)
        self.env.invalidate_all()
        self.assertEqual(account.registration, 'pantalytics')


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestReconnectThroughTheApp(HttpCase):
    """The consent records its app, and a reconnect moves the account."""

    def setUp(self):
        super().setUp()
        self.env['pan.mail.domain'].set_domains(['company.test'])
        self.row = own_row(self.env)
        self.nora = make_user(self.env, 'nora@company.test')
        self.authenticate('nora@company.test', 'nora@company.test')

    def _grant(self, tenant=OWN_TENANT, refresh='rt-new'):
        tokens = {'access_token': 'at-new', 'refresh_token': refresh,
                  'token_expiry': '2030-01-01 00:00:00'}
        identity = {'email': 'nora@company.test', 'name': None,
                    'provider_user_id': 'oid-nora', 'principal_name': 'nora@company.test',
                    'tenant_id': tenant}
        return (patch(f'{GRAPH}._exchange_code_for_tokens', return_value=tokens),
                patch(f'{GRAPH}.read_user_info', return_value=identity))

    def _own_callback(self):
        self.nora.sudo().x_pan_mail_oauth_state = 'nonce-1'
        return self.url_open('/microsoft_oauth/callback?code=authcode&state=nonce-1')

    def _arm_app_round(self):
        self.session[MS_APP_SESSION_KEY] = {
            'state': 'platform-state-1', 'code_verifier': 'verifier-1',
            'redirect_uri': PLATFORM_REDIRECT, 'admin_consent': False}
        odoo.http.root.session_store.save(self.session)

    def _account(self):
        return self.env['pan.mail.account'].sudo().search([('user_id', '=', self.nora.id)])

    def test_a_sign_in_through_the_own_app_is_recorded_as_own(self):
        exchange, me = self._grant()
        with exchange as redeem, me:
            response = self._own_callback()
        self.assertIn('Mailbox Connected', response.text)
        self.assertEqual(self._account().registration, 'own')
        self.assertEqual(redeem.call_args.kwargs.get('registration'), 'own')

    def test_reconnecting_through_the_app_moves_the_account(self):
        account = connected_account(self.env, self.nora)
        connect_to_pantalytics(self.env)
        self.row.write({'registration': 'pantalytics', 'pantalytics_client_id': APP_ID})
        self.assertTrue(self.nora._pan_mail_should_prompt_reconnect())
        info = self.make_jsonrpc_request('/web/session/get_session_info', {})
        self.assertTrue(info['pan_mail_reconnect_prompt'])
        self.assertFalse(info['pan_mail_connect_prompt'])

        self._arm_app_round()
        exchange, me = self._grant()
        with exchange as redeem, me:
            response = self.url_open(
                '/mail_pro/microsoft/return?code=authcode&state=platform-state-1')
        self.assertIn('Mailbox Connected', response.text)
        redeem.assert_called_once_with('authcode', PLATFORM_REDIRECT,
                                       code_verifier='verifier-1', registration='pantalytics')
        account.invalidate_recordset()
        self.assertEqual(self._account(), account, 'the same row, not a second one')
        self.assertEqual(account.registration, 'pantalytics')
        self.assertEqual(account.sudo().refresh_token, 'rt-new')
        self.assertFalse(self.nora._pan_mail_should_prompt_reconnect())
        info = self.make_jsonrpc_request('/web/session/get_session_info', {})
        self.assertFalse(info['pan_mail_reconnect_prompt'])

    def test_a_reconnect_from_another_tenant_is_refused_and_changes_nothing(self):
        account = connected_account(self.env, self.nora)
        connect_to_pantalytics(self.env)
        self.row.write({'registration': 'pantalytics', 'pantalytics_client_id': APP_ID})
        self._arm_app_round()
        exchange, me = self._grant(tenant=OTHER_TENANT)
        with exchange, me:
            response = self.url_open(
                '/mail_pro/microsoft/return?code=authcode&state=platform-state-1')
        self.assertIn('Connection Failed', response.text)
        account.invalidate_recordset()
        self.assertEqual(account.registration, 'own')
        self.assertEqual(account.sudo().refresh_token, 'rt-old')

    def test_a_round_started_before_the_switch_finishes_on_the_own_app(self):
        account = connected_account(self.env, self.nora, refresh_token=False)
        self.nora.sudo().x_pan_mail_oauth_state = 'nonce-1'
        connect_to_pantalytics(self.env)
        self.row.write({'registration': 'pantalytics', 'pantalytics_client_id': APP_ID})
        exchange, me = self._grant()
        with exchange as redeem, me:
            self.url_open('/microsoft_oauth/callback?code=authcode&state=nonce-1')
        redeem.assert_called_once_with(
            'authcode', oauth_redirect_uri(self.env, 'outlook'), registration='own')
        account.invalidate_recordset()
        self.assertEqual(account.registration, 'own')
        self.assertTrue(account.connected)
