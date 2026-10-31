# -*- coding: utf-8 -*-
"""Signing in with Microsoft through the Pantalytics Mail Pro app.

A Microsoft 365 customer registers nothing in Azure: the sign-in goes through
our multitenant public client. Our platform starts each round (the state, the
app's client id, its redirect URI), the module builds the authorization URL
with a PKCE challenge whose verifier stays in the session, Microsoft answers
on the platform's callback, and the platform sends the browser on to
`/mail_pro/microsoft/return`, which is the one OAuth callback again with no
secret. The customer's own app registration keeps working exactly as before.

The platform is faked at `requests.post` in `pan_mail_license`, Microsoft at
the client's own methods or at `requests.post` in the Graph client: nothing
here leaves the test process.
"""
import base64
import contextlib
import hashlib
import types
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import odoo
from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.pan_mail_pro.models import encryption_utils
from odoo.addons.pan_mail_pro.models.mail_provider_client import get_provider_client
from odoo.addons.pan_mail_pro.models.pan_mail_provider import (
    MS_APP_RETURN_PATH, MS_APP_SESSION_KEY,
)
from odoo.addons.pan_mail_pro.tests.common import _FakeSession

PLATFORM_POST = 'odoo.addons.pan_mail_pro.models.pan_mail_license.requests.post'
GRAPH_POST = 'odoo.addons.pan_mail_pro.models.providers.microsoft.graph_client.requests.post'
GRAPH = 'odoo.addons.pan_mail_pro.models.providers.microsoft.graph_client.MicrosoftGraphClient'

APP_ID = 'aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb'
PLATFORM_REDIRECT = 'https://mcp.pantalytics.com/admin/mail-pro/ms/callback'
KEY = 'mpk_live_installation_key'


def _response(status_code, body):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = body
    return response


def _started(state='platform-state-1'):
    return _response(200, {'status': 'ok', 'state': state, 'client_id': APP_ID,
                           'redirect_uri': PLATFORM_REDIRECT})


def _challenge_of(verifier):
    digest = hashlib.sha256(verifier.encode('ascii')).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')


def connect_to_pantalytics(env, status='active'):
    """Step 1 answered: a link with a key, as the pairing leaves it."""
    return env['pan.mail.license'].sudo().create({
        'status': status,
        'key_encrypted': encryption_utils.encrypt_value(env, KEY),
        'valid_until': fields.Datetime.add(fields.Datetime.now(), days=14),
    })


@contextlib.contextmanager
def a_browser(env):
    """A request with a session and nothing else, which is all a sign-in
    start writes to (tests/common.py `proven_session` is the same idea)."""
    session = _FakeSession()
    session.uid = env.user.id
    odoo.http._request_stack.push(types.SimpleNamespace(session=session, env=env, cookies={}))
    try:
        yield session
    finally:
        odoo.http._request_stack.pop()


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestPantalyticsAppRow(TransactionCase):
    """Whose app the provider row signs in through, and what that answers."""

    def setUp(self):
        super().setUp()
        self.Provider = self.env['pan.mail.provider']

    def test_a_new_microsoft_row_signs_in_through_the_pantalytics_app(self):
        row = self.Provider.create({'provider': 'outlook'})
        self.assertEqual(row.registration, 'pantalytics')
        self.assertTrue(row.uses_pantalytics_app())
        self.assertFalse(row.redirect_uri, 'nothing to paste into Azure')

    def test_a_row_created_with_a_registration_is_an_own_app_row(self):
        """An import, a script or the seed that brings an Azure registration
        along keeps it: making it the Pantalytics app would drop it."""
        row = self.Provider.create({
            'provider': 'outlook', 'client_id': 'id', 'client_secret': 'secret',
            'tenant_id': '11111111-2222-3333-4444-555555555555'})
        self.assertEqual(row.registration, 'own')
        self.assertFalse(row.uses_pantalytics_app())
        self.assertTrue(row.redirect_uri.endswith('/microsoft_oauth/callback'))

    def test_only_microsoft_has_a_pantalytics_app(self):
        row = self.Provider.create({'provider': 'gmail'})
        self.assertFalse(row.uses_pantalytics_app())
        self.assertFalse(self.Provider._pantalytics_row('gmail'))

    def test_the_step_is_answered_once_this_odoo_is_connected(self):
        row = self.Provider.create({'provider': 'outlook'})
        self.assertFalse(row._credentials_present(),
                         'without a Pantalytics connection the app cannot be used')
        connect_to_pantalytics(self.env)
        row.invalidate_recordset()
        self.assertTrue(row._credentials_present())
        self.assertTrue(self.env['pan.mail.setup'].credentials_set('outlook'))
        self.assertEqual(row.status, 'not_connected', 'answered, nobody signed in yet')

    def test_a_pending_or_refused_key_is_not_a_connection(self):
        row = self.Provider.create({'provider': 'outlook'})
        link = connect_to_pantalytics(self.env, status='pending')
        self.assertFalse(row._credentials_present())
        link.status = 'invalid'
        self.assertFalse(row._credentials_present())

    def test_a_lapsed_connection_keeps_what_a_refresh_needs(self):
        """The app's id from an earlier sign-in is all a refresh needs, and
        sending is never gated on the connection (§9.17)."""
        row = self.Provider.create({'provider': 'outlook', 'pantalytics_client_id': APP_ID})
        self.assertTrue(row._credentials_present())

    def test_the_form_check_points_at_step_1_without_a_connection(self):
        row = self.Provider.create({'provider': 'outlook'})
        answer = row.verify_registration()
        self.assertIs(answer['verified'], False)
        self.assertIn('step 1', answer['message'])
        connect_to_pantalytics(self.env)
        answer = row.verify_registration()
        self.assertIsNone(answer['verified'], 'signing in is the check')

    def test_step_2_is_one_click_from_the_settings_page(self):
        connect_to_pantalytics(self.env)
        settings = self.env['res.config.settings'].create({})
        settings.action_use_microsoft_app()
        row = self.Provider.current()
        self.assertEqual((row.provider, row.registration), ('outlook', 'pantalytics'))
        settings = self.env['res.config.settings'].create({})
        self.assertTrue(settings.x_setup_provider_done)
        settings.action_use_microsoft_app()
        self.assertEqual(self.Provider.search_count([]), 1, 'a second click adds nothing')


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestMicrosoftStart(TransactionCase):
    """The platform call, and the authorization URL built from its answer."""

    def setUp(self):
        super().setUp()
        self.env['pan.mail.domain'].set_domains(['company.test'])
        self.row = self.env['pan.mail.provider'].create({'provider': 'outlook'})
        self.calls = []

    def platform(self, response=None):
        def handler(url, json=None, headers=None, timeout=None):
            self.calls.append({'url': url, 'json': json, 'headers': headers or {}})
            return response or _started()
        return patch(PLATFORM_POST, side_effect=handler)

    def test_the_start_is_sent_under_the_installation_key(self):
        connect_to_pantalytics(self.env)
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        with self.platform():
            answer = self.env['pan.mail.license'].microsoft_start(base + MS_APP_RETURN_PATH)
        self.assertEqual(answer, {'state': 'platform-state-1', 'client_id': APP_ID,
                                  'redirect_uri': PLATFORM_REDIRECT})
        call, = self.calls
        self.assertTrue(call['url'].endswith('/api/v1/mail-pro/microsoft/start'))
        self.assertEqual(call['headers'], {'Authorization': f'Bearer {KEY}'})
        self.assertEqual(call['json'], {'return_url': base + '/mail_pro/microsoft/return'})

    def test_no_key_no_call_and_a_pointer_to_step_1(self):
        with self.platform(), self.assertRaisesRegex(UserError, 'step 1'):
            self.env['pan.mail.license'].microsoft_start('https://odoo.test/x')
        self.assertFalse(self.calls)

    def test_the_platform_s_refusals_are_sentences(self):
        connect_to_pantalytics(self.env)
        for status, body, words in (
                (401, {'status': 'invalid'}, 'no longer recognises'),
                (503, {'status': 'not_configured'}, 'not available right now'),
                (400, {'status': 'bad_request'}, 'HTTP 400'),
                (200, {'status': 'ok', 'state': 's'}, 'does not understand'),
                (200, {'status': 'ok', 'state': 's', 'client_id': APP_ID,
                       'redirect_uri': 'http://plain.test/cb'}, 'does not understand')):
            with self.subTest(status=status, body=body):
                with self.platform(_response(status, body)), \
                        self.assertRaisesRegex(UserError, words):
                    self.env['pan.mail.license'].microsoft_start('https://odoo.test/x')

    def test_connect_goes_to_microsoft_in_the_same_tab_with_pkce(self):
        connect_to_pantalytics(self.env)
        user = self.env.user
        user.sudo().x_pan_mail_oauth_state = 'untouched'
        with self.platform(), a_browser(self.env) as session:
            action = user.action_connect_mailbox('outlook')
        self.assertEqual(action['target'], 'self',
                         'a new tab after an awaited call is blocked by Brave and Safari')
        url = urlparse(action['url'])
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        self.assertEqual(url.netloc, 'login.microsoftonline.com')
        self.assertEqual(url.path, '/organizations/oauth2/v2.0/authorize')
        self.assertEqual(query['client_id'], APP_ID)
        self.assertEqual(query['redirect_uri'], PLATFORM_REDIRECT)
        self.assertEqual(query['state'], 'platform-state-1')
        self.assertEqual(query['code_challenge_method'], 'S256')
        scopes = query['scope'].split()
        for scope in ('Mail.Send', 'offline_access', 'MailboxSettings.Read',
                      'User.ReadBasic.All'):
            self.assertIn(scope, scopes, 'the app carries the inspection scopes')

        pending = session[MS_APP_SESSION_KEY]
        self.assertEqual(pending['state'], 'platform-state-1')
        self.assertEqual(pending['redirect_uri'], PLATFORM_REDIRECT)
        self.assertEqual(_challenge_of(pending['code_verifier']), query['code_challenge'])
        self.assertNotIn(pending['code_verifier'], action['url'])
        self.assertEqual(self.row.pantalytics_client_id, APP_ID, 'kept for the refresh')
        self.assertEqual(user.sudo().x_pan_mail_oauth_state, 'untouched',
                         'the proof is in the session, not on the user row')

    def test_connect_without_a_connection_says_so(self):
        with self.platform(), a_browser(self.env), \
                self.assertRaisesRegex(UserError, 'connected to Pantalytics'):
            self.env.user.action_connect_mailbox('outlook')
        self.assertFalse(self.calls)

    def test_the_organization_approval_url(self):
        connect_to_pantalytics(self.env)
        with self.platform(_started('platform-state-2')), a_browser(self.env) as session:
            url, state = self.row._pantalytics_sign_in(admin_consent=True)
        parsed = urlparse(url)
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        self.assertEqual(parsed.netloc + parsed.path,
                         'login.microsoftonline.com/organizations/v2.0/adminconsent')
        self.assertEqual(query, {'client_id': APP_ID,
                                 'scope': 'https://graph.microsoft.com/.default',
                                 'redirect_uri': PLATFORM_REDIRECT,
                                 'state': 'platform-state-2'})
        self.assertEqual(state, 'platform-state-2')
        self.assertTrue(session[MS_APP_SESSION_KEY]['admin_consent'])
        self.assertFalse(session[MS_APP_SESSION_KEY]['code_verifier'])

    def test_a_start_without_a_browser_is_refused(self):
        connect_to_pantalytics(self.env)
        with self.platform(), self.assertRaises(UserError):
            self.row._pantalytics_sign_in()
        self.assertFalse(self.calls, 'refused before the platform is asked')


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestPublicClientTokens(TransactionCase):
    """The code and the refresh go to Microsoft with no secret."""

    def setUp(self):
        super().setUp()
        self.client = get_provider_client(self.env, 'outlook')
        self.posted = []

    def microsoft(self):
        def handler(url, data=None, timeout=None, **kwargs):
            self.posted.append({'url': url, 'data': dict(data or {})})
            return _response(200, {'access_token': 'at-new', 'refresh_token': 'rt-new',
                                   'expires_in': 3600, 'scope': 'Mail.Send'})
        return patch(GRAPH_POST, side_effect=handler)

    def test_the_code_is_redeemed_with_the_verifier_and_no_secret(self):
        self.env['pan.mail.provider'].create({'provider': 'outlook',
                                              'pantalytics_client_id': APP_ID})
        with self.microsoft():
            tokens = self.client._exchange_code_for_tokens(
                'authcode', PLATFORM_REDIRECT, code_verifier='verifier-1')
        self.assertEqual(tokens['access_token'], 'at-new')
        post, = self.posted
        self.assertEqual(post['url'],
                         'https://login.microsoftonline.com/organizations/oauth2/v2.0/token')
        self.assertEqual(post['data'], {
            'client_id': APP_ID, 'code': 'authcode', 'redirect_uri': PLATFORM_REDIRECT,
            'grant_type': 'authorization_code', 'code_verifier': 'verifier-1'})

    def test_the_refresh_needs_only_the_client_id(self):
        self.env['pan.mail.provider'].create({'provider': 'outlook',
                                              'pantalytics_client_id': APP_ID})
        account = self.env['pan.mail.account'].create({
            'email': 'nora@company.test', 'provider': 'outlook',
            'refresh_token': 'rt-old', 'access_token': 'at-old'})
        with self.microsoft():
            self.assertEqual(self.client.refresh_access_token(account), 'at-new')
        post, = self.posted
        self.assertNotIn('client_secret', post['data'])
        self.assertEqual(post['data']['client_id'], APP_ID)
        self.assertEqual(post['data']['grant_type'], 'refresh_token')
        self.assertEqual(account.sudo().refresh_token, 'rt-new')

    def test_an_own_app_still_sends_its_secret_and_no_verifier(self):
        self.env['pan.mail.provider'].create({
            'provider': 'outlook', 'client_id': 'own-id', 'client_secret': 'own-secret',
            'tenant_id': '11111111-2222-3333-4444-555555555555'})
        with self.microsoft():
            self.client._exchange_code_for_tokens('authcode', 'https://odoo.test/cb')
        post, = self.posted
        self.assertIn('/11111111-2222-3333-4444-555555555555/', post['url'])
        self.assertEqual(post['data']['client_secret'], 'own-secret')
        self.assertNotIn('code_verifier', post['data'])

    def test_an_own_app_asks_no_inspection_scope_and_no_challenge(self):
        """The opt-in of 19.0.28.1.0 stands for a registration the customer owns."""
        self.env['pan.mail.provider'].create({
            'provider': 'outlook', 'client_id': 'own-id', 'client_secret': 'own-secret',
            'tenant_id': '11111111-2222-3333-4444-555555555555'})
        url = self.client.get_authorization_url('https://odoo.test/cb', state='s')
        self.assertNotIn('MailboxSettings.Read', url)
        self.assertNotIn('code_challenge', url)

    def test_which_refusals_ask_for_an_administrator(self):
        self.assertTrue(self.client.needs_admin_consent('consent_required'))
        self.assertTrue(self.client.needs_admin_consent(
            'access_denied', 'AADSTS90094: The grant requires admin permission.'))
        self.assertTrue(self.client.needs_admin_consent(
            'invalid_client', 'AADSTS65001: The user or administrator has not consented'))
        self.assertFalse(self.client.needs_admin_consent('access_denied', 'User said no'))

    def test_a_sign_in_from_another_directory_than_the_connected_one_is_refused(self):
        """The app is multitenant, so the registration names no tenant. The
        directory the connected mailboxes belong to is the one this Odoo
        serves."""
        self.env['pan.mail.provider'].create({'provider': 'outlook',
                                              'pantalytics_client_id': APP_ID})
        self.assertIsNone(self.client.identity_refusal({'tenant_id': 'tenant-a'}),
                          'the first sign-in sets it')
        self.env['pan.mail.account'].create({
            'email': 'nora@company.test', 'provider': 'outlook', 'tenant_id': 'Tenant-A',
            'refresh_token': 'rt', 'access_token': 'at'})
        self.assertIsNone(self.client.identity_refusal({'tenant_id': 'tenant-a'}))
        self.assertIn('tenant-b', self.client.identity_refusal({'tenant_id': 'tenant-b'}))


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestMicrosoftReturn(HttpCase):
    """`/mail_pro/microsoft/return`: the platform hands Microsoft's answer back."""

    def setUp(self):
        super().setUp()
        self.env['pan.mail.domain'].set_domains(['company.test'])
        self.row = self.env['pan.mail.provider'].create({
            'provider': 'outlook', 'pantalytics_client_id': APP_ID})
        connect_to_pantalytics(self.env)
        self.user = self.env['res.users'].create({
            'name': 'Nora Employee', 'login': 'nora@company.test',
            'password': 'nora@company.test', 'email': 'nora@company.test',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        self.authenticate('nora@company.test', 'nora@company.test')

    # ------------------------------------------------------------------ helpers

    def _stored(self):
        return odoo.http.root.session_store.get(self.session.sid)

    def _arm(self, state='platform-state-1', admin_consent=False):
        """A round in flight, as `_pantalytics_sign_in` leaves it."""
        self.session[MS_APP_SESSION_KEY] = {
            'state': state, 'code_verifier': False if admin_consent else 'verifier-1',
            'redirect_uri': PLATFORM_REDIRECT, 'admin_consent': admin_consent}
        odoo.http.root.session_store.save(self.session)

    def _return(self, **params):
        query = '&'.join(f'{key}={value}' for key, value in params.items())
        return self.url_open(f'/mail_pro/microsoft/return?{query}', allow_redirects=False)

    def _grant(self, email='nora@company.test'):
        tokens = {'access_token': 'at', 'refresh_token': 'rt',
                  'token_expiry': '2030-01-01 00:00:00'}
        identity = {'email': email, 'name': None, 'provider_user_id': 'oid-nora',
                    'principal_name': email, 'tenant_id': 'tenant-a'}
        return (patch(f'{GRAPH}._exchange_code_for_tokens', return_value=tokens),
                patch(f'{GRAPH}.read_user_info', return_value=identity))

    def _accounts(self):
        return self.env['pan.mail.account'].sudo().search([('user_id', '=', self.user.id)])

    # ------------------------------------------------------------------ the code

    def test_the_code_is_redeemed_with_this_session_s_verifier(self):
        self._arm()
        exchange, me = self._grant()
        with exchange as redeem, me:
            response = self._return(code='authcode', state='platform-state-1')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Mailbox Connected', response.text)
        redeem.assert_called_once_with('authcode', PLATFORM_REDIRECT,
                                       code_verifier='verifier-1', registration='pantalytics')
        account = self._accounts()
        self.assertEqual(account.email, 'nora@company.test')
        self.assertEqual(account.sudo().refresh_token, 'rt')
        self.assertNotIn(MS_APP_SESSION_KEY, self._stored(), 'the state is spent')
        self.assertIn('Back to Odoo', response.text,
                      'same tab: the page leads back instead of ending')

    def test_a_state_this_session_did_not_start_is_refused(self):
        self._arm()
        exchange, me = self._grant()
        with exchange as redeem, me:
            response = self._return(code='authcode', state='somebody-elses-state')
        self.assertIn('Security validation failed', response.text)
        redeem.assert_not_called()
        self.assertFalse(self._accounts())
        self.assertIn(MS_APP_SESSION_KEY, self._stored(),
                      'a forged answer does not cancel the round in flight')

    def test_no_round_in_flight_is_refused(self):
        exchange, me = self._grant()
        with exchange as redeem, me:
            response = self._return(code='authcode', state='platform-state-1')
        self.assertIn('Security validation failed', response.text)
        redeem.assert_not_called()

    def test_a_replayed_answer_is_refused(self):
        self._arm()
        exchange, me = self._grant()
        with exchange as redeem, me:
            self._return(code='authcode', state='platform-state-1')
            replay = self._return(code='authcode', state='platform-state-1')
        self.assertIn('Security validation failed', replay.text)
        self.assertEqual(redeem.call_count, 1)

    def test_microsoft_s_refusal_reaches_the_person(self):
        self._arm()
        response = self._return(error='access_denied', state='platform-state-1',
                                error_description='The+user+cancelled')
        self.assertIn('Connection Failed', response.text)
        self.assertIn('The user cancelled', response.text)
        self.assertFalse(self._accounts())

    def test_a_tenant_that_wants_an_administrator_is_offered_the_approval(self):
        self._arm()
        response = self._return(error='access_denied', state='platform-state-1',
                                error_description='AADSTS90094%3A+admin+approval+required')
        self.assertIn('Approval needed', response.text)
        self.assertIn('/mail_pro/connect?admin_consent=1', response.text)
        self.assertFalse(self._accounts())

    # ------------------------------------------------------------------ the approval

    def test_an_administrator_s_approval_is_answered(self):
        self._arm(admin_consent=True)
        response = self._return(admin_consent='True', tenant='tenant-a',
                                state='platform-state-1')
        self.assertIn('Approved', response.text)
        self.assertIn('/mail_pro/connect', response.text)
        self.assertNotIn(MS_APP_SESSION_KEY, self._stored())

    def test_a_refused_approval_says_so(self):
        self._arm(admin_consent=True)
        response = self._return(error='access_denied', state='platform-state-1',
                                error_description='Declined')
        self.assertIn('Not approved', response.text)
        self.assertIn('Declined', response.text)

    # ------------------------------------------------------------------ the start

    def test_the_connect_link_starts_at_the_platform(self):
        with patch(PLATFORM_POST, return_value=_started('platform-state-9')):
            response = self.url_open('/mail_pro/connect', allow_redirects=False)
        self.assertIn(response.status_code, (302, 303))
        location = urlparse(response.headers['Location'])
        self.assertEqual(location.netloc, 'login.microsoftonline.com')
        self.assertEqual(parse_qs(location.query)['state'], ['platform-state-9'])
        self.assertEqual(self._stored()[MS_APP_SESSION_KEY]['state'], 'platform-state-9')

    def test_the_approval_link_starts_at_the_platform(self):
        with patch(PLATFORM_POST, return_value=_started('platform-state-8')):
            response = self.url_open('/mail_pro/connect?admin_consent=1',
                                     allow_redirects=False)
        self.assertIn(response.status_code, (302, 303))
        self.assertIn('/organizations/v2.0/adminconsent', response.headers['Location'])
        self.assertTrue(self._stored()[MS_APP_SESSION_KEY]['admin_consent'])

    def test_without_a_connection_the_link_says_where_to_connect(self):
        self.env['pan.mail.license'].sudo().search([]).unlink()
        self.row.pantalytics_client_id = False
        with patch(PLATFORM_POST) as post:
            response = self.url_open('/mail_pro/connect', allow_redirects=False)
        post.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertIn('Settings, Mail Pro, step 1', response.text)
