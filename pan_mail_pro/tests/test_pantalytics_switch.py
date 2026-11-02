# -*- coding: utf-8 -*-
"""Moving a Microsoft 365 customer off their own Azure app to ours.

A refresh token is bound to the client id that issued it, so no token moves
silently: every person signs in once more, through the Pantalytics app. What
this file guards is that nothing stops in between. Each account remembers the
app its token came from (`pan.mail.account.registration`) and refreshes with
that one; the switch keeps the own app's credentials on the row for them;
the people still on it are nudged to reconnect; and the old credentials go
on their own once nobody uses them, never before (ARCHITECTURE.md §9.20).

The platform is faked at `requests.post` in `pan_mail_license`, Microsoft at
the Graph client's own methods or at its `requests.post`.
"""
import importlib.util
import os
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import odoo
from lxml import etree
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
        """A row put back on the own app (a technical write; the form has no
        choice since #369), and the accounts the app signed in keep
        refreshing with it: the account decides."""
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

    def test_the_step_two_arrow_opens_the_row_not_a_new_one(self):
        """Step 2's arrow opens the existing row as a form (#369). On
        odoo.pantalytics.com it landed on a new, unsaved provider form, and
        saving that is a second row."""
        settings = self.env['res.config.settings'].with_user(self.admin).create({})
        action = settings.action_open_provider()
        self.assertEqual(action['res_model'], 'pan.mail.provider')
        self.assertEqual(action['res_id'], self.row.id)
        self.assertEqual(action['view_mode'], 'form')
        self.assertEqual([kind for _view, kind in action['views']], ['form'])
        self.assertNotIn('default_provider', action.get('context') or '')

    def test_without_a_row_the_arrow_opens_the_list(self):
        """Nothing to open yet: the Providers list, where the empty state
        says what to add. Never a form in create mode."""
        self.row.unlink()
        action = self.env['res.config.settings'].with_user(self.admin).create(
            {}).action_open_provider()
        self.assertFalse(action.get('res_id'))
        self.assertEqual(action['view_mode'].split(',')[0], 'list')

    def test_the_providers_menu_opens_the_list(self):
        """The XML action itself starts on the list, not on a form."""
        action = self.env['ir.actions.act_window']._for_xml_id(
            'pan_mail_pro.action_pan_mail_provider')
        self.assertEqual(action['view_mode'].split(',')[0], 'list')
        self.assertFalse(action.get('res_id'))

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

    def _gone(self):
        self.row.invalidate_recordset()
        return not (self.row.client_id or self.row.tenant_id
                    or self.row.sudo().client_secret_encrypted)

    def test_the_old_app_goes_when_the_last_account_signs_in_again(self):
        """No button (#369): the own app's credentials stay while anybody
        refreshes with them, and go the moment the last one signs in again
        through ours."""
        self._switch()
        self.row.invalidate_recordset()
        self.assertEqual(self.row.own_account_count, 2)
        self.nora_account.registration = 'pantalytics'
        self.assertFalse(self._gone(), 'Mia still refreshes with it')
        self.mia_account.registration = 'pantalytics'
        self.assertTrue(self._gone())
        self.assertFalse(self.row.has_own_registration)
        self.assertEqual(self.row.registration, 'pantalytics')

    def test_a_disconnect_or_a_removal_counts_too(self):
        """Leaving the own app is not only signing in again: an account that
        is disconnected or removed refreshes with nothing."""
        self._switch()
        self.nora_account.registration = 'pantalytics'
        self.mia.action_disconnect_mailbox()
        self.assertTrue(self._gone())

    def test_a_removed_last_account_clears_it(self):
        self._switch()
        self.nora_account.registration = 'pantalytics'
        self.mia_account.unlink()
        self.assertTrue(self._gone())

    def test_the_daily_cron_is_the_net(self):
        """A database that moved before this release, or a path that wrote
        the account without its `write`: the heartbeat cron clears it."""
        self._switch()
        self.env.cr.execute(
            "UPDATE pan_mail_account SET registration = 'pantalytics' WHERE id IN %s",
            [tuple((self.nora_account | self.mia_account).ids)])
        (self.nora_account | self.mia_account).invalidate_recordset()
        self.assertFalse(self._gone(), 'nothing wrote through the ORM yet')
        with patch.object(type(self.env['pan.mail.license']), '_heartbeat_guarded'):
            self.env['pan.mail.license']._cron_heartbeat()
        self.assertTrue(self._gone())

    def test_an_own_row_keeps_the_app_it_signs_in_with(self):
        self.nora_account.registration = 'pantalytics'
        self.mia_account.registration = 'pantalytics'
        self.assertFalse(self.env['pan.mail.provider']._forget_unused_own_registration())
        self.assertEqual(self.row.client_id, 'own-id')

    def test_step_two_is_amber_while_the_row_is_on_its_own_app(self):
        """The dot on step 2 follows the row only (#369): amber while it is
        on its own app, green once it has moved, whoever still refreshes
        through the old app. That is each mailbox's status."""
        settings = self._settings()
        self.assertTrue(settings.x_setup_provider_done)
        self.assertTrue(settings.x_microsoft_on_own_app)
        self._switch()
        self.row.invalidate_recordset()
        self.assertEqual(self.row.own_account_count, 2, 'two people still on the own app')
        settings = self._settings()
        self.assertFalse(settings.x_microsoft_on_own_app, 'and yet the step is green')
        self.assertTrue(settings.x_setup_provider_done)

    def _arch_text(self, model, xmlid, root_xpath=None):
        arch = self.env[model].get_view(self.env.ref(xmlid).id, 'form')['arch']
        root = etree.fromstring(arch)
        if root_xpath:
            root = root.xpath(root_xpath)[0]
        for comment in root.xpath('.//comment()'):
            comment.getparent().remove(comment)
        return root, etree.tostring(root, encoding='unicode')

    def test_step_two_reports_and_does_not_act(self):
        """The overview shows the status, the provider form holds the action
        (#369): on an own app step 2 is an amber dot and one line, with no
        button, no count and no cleanup. Pantalytics is not an email
        provider, so its app has no name on the settings page (#366)."""
        app, page = self._arch_text('res.config.settings',
                                    'pan_mail_pro.res_config_settings_view_form',
                                    "//app[@name='pan_mail_pro']")
        self.assertNotIn('Pantalytics app', page)
        self.assertIn('Sign in again to finish the move', page)
        self.assertIn('o_mailpro_dot_attention', page, 'amber, not the red of broken')
        for gone in ('Sign in with Microsoft', 'Remove your old app registration',
                     'still has to sign in', 'still have to sign in', 'no longer needed'):
            self.assertNotIn(gone, page)
        arrows = app.xpath("//button[@name='action_open_provider']")
        self.assertEqual(len(arrows), 1, 'the arrow opens the row through a method')
        self.assertEqual(arrows[0].get('type'), 'object')

    def test_the_provider_form_holds_the_move(self):
        """The provider form has the move and nothing to choose (#369): no
        "Sign in through" radio, the deprecation in one line and Sign in with
        Microsoft without a confirm. Nothing about people: who still has to
        sign in again is each mailbox's status, and the cleanup is automatic."""
        form, page = self._arch_text('pan.mail.provider',
                                     'pan_mail_pro.view_pan_mail_provider_form')
        self.assertNotIn('Pantalytics app', page)
        self.assertNotIn('Pantalytics Mail Pro app', page)
        self.assertNotIn('Nothing to register in Microsoft Entra', page)
        self.assertIn('Your own app registration is deprecated.', page)
        for gone in ('own_account_count', 'Remove your old app registration',
                     'action_remove_own_registration', 'sign in again'):
            self.assertNotIn(gone, page)
        registration, = form.xpath("//field[@name='registration']")
        self.assertEqual(registration.get('invisible'), '1', 'no choice on screen')
        switch, = form.xpath("//button[@name='action_switch_to_pantalytics']")
        self.assertEqual(switch.get('string'), 'Sign in with Microsoft')
        self.assertFalse(switch.get('confirm'), 'it only sends the admin to Microsoft')
        for name in ('client_id', 'tenant_id', 'client_secret'):
            microsoft = [f for f in form.xpath(f"//field[@name='{name}']")
                         if f.get('invisible') == "provider != 'outlook'"]
            self.assertTrue(microsoft and microsoft[0].get('readonly') == '1',
                            f"the own app's {name} is read-only")


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
        return self.url_open('/microsoft_oauth/callback?code=authcode&state=nonce-1',
                             allow_redirects=False)

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
        self.assertEqual(response.status_code, 303)
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
                '/mail_pro/microsoft/return?code=authcode&state=platform-state-1',
                allow_redirects=False)
        self.assertEqual(response.status_code, 303)
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
