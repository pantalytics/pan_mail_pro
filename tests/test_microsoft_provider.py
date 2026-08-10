# -*- coding: utf-8 -*-
"""Microsoft Graph client: the OAuth token lifecycle.

The rest of the Graph client is covered indirectly by the routing, composer and
sync suites, which drive it through `mock_graph` in common.py. That fixture
stubs `get_valid_token` outright, so the token lifecycle itself — refresh,
rotation, expiry buffer, revocation — was the one part of the provider that
nothing exercised. Gmail had these tests from the start; Microsoft is the
provider customers actually run, so it needs the same floor.

Mirrors `test_google_provider.py::TestGoogleProvider` token-lifecycle block on
purpose: where the two providers must behave the same, the tests read the same,
and the places they legitimately differ (Microsoft rotates the refresh token,
Google usually omits it) are asserted rather than assumed.

HTTP is mocked at the requests boundary. No real network, no Azure app.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import requests

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.pan_mail_pro.models.mail_provider_client import get_provider_client

# Patch requests.post specifically, not the whole module — the client catches
# requests.exceptions.RequestException, which must stay a real class.
GRAPH_POST = 'odoo.addons.pan_mail_pro.models.providers.microsoft.graph_client.requests.post'


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestMicrosoftTokenLifecycle(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Account = cls.env['pan.mail.account']
        cls.client = get_provider_client(cls.env, 'outlook')
        cls.user = cls.env['res.users'].create({
            'name': 'Graph User', 'login': 'graph_user@test.local',
            'email': 'graph_user@test.local',
        })
        ICP = cls.env['ir.config_parameter'].sudo()
        ICP.set_param('x_pan_outlook_pro.client_id', 'test-client-id')
        ICP.set_param('x_pan_outlook_pro.tenant_id', 'test-tenant-id')

    def _account(self, **vals):
        base = {
            'email': 'graph_user@test.local',
            'provider': 'outlook',
            'user_id': self.user.id,
        }
        base.update(vals)
        return self.Account.create(base)

    def _ok_response(self, payload):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = payload
        return resp

    def _http_error(self, payload):
        """A real requests exception carrying a Microsoft error body."""
        resp = MagicMock()
        resp.json.return_value = payload
        exc = requests.exceptions.RequestException('400 Client Error')
        exc.response = resp
        return exc

    # ------------------------------------------------------------------ #
    # Code exchange
    # ------------------------------------------------------------------ #
    def test_exchange_code_returns_tokens(self):
        with patch(GRAPH_POST, return_value=self._ok_response(
                {'access_token': 'AT', 'refresh_token': 'RT', 'expires_in': 3600})):
            tokens = self.client._exchange_code_for_tokens('code', 'https://odoo.test/cb')

        self.assertEqual(tokens['access_token'], 'AT')
        self.assertEqual(tokens['refresh_token'], 'RT')
        self.assertGreater(tokens['token_expiry'], datetime.now())

    def test_exchange_code_failure_raises_user_error(self):
        """A failed exchange must surface as a UserError, not a raw HTTP error:
        it happens inside the OAuth callback, where the user is watching."""
        with patch(GRAPH_POST, side_effect=self._http_error(
                {'error': 'invalid_client', 'error_description': 'bad secret'})):
            with self.assertRaises(UserError):
                self.client._exchange_code_for_tokens('code', 'https://odoo.test/cb')

    # ------------------------------------------------------------------ #
    # get_valid_token
    # ------------------------------------------------------------------ #
    def test_get_valid_token_returns_live_token_without_refresh(self):
        account = self._account(
            access_token='still-good', refresh_token='r',
            token_expiry=datetime.now() + timedelta(hours=1))
        with patch(GRAPH_POST) as post:
            token = self.client.get_valid_token(account)
            post.assert_not_called()
        self.assertEqual(token, 'still-good')

    def test_token_inside_the_five_minute_buffer_is_refreshed_early(self):
        """A token valid for two more minutes is refreshed now.

        The buffer is the point: a send that starts with three minutes left
        must not have the token expire mid-flight, between the draft POST and
        the send POST.
        """
        account = self._account(
            access_token='nearly-stale', refresh_token='the-refresh',
            token_expiry=datetime.now() + timedelta(minutes=2))
        with patch(GRAPH_POST, return_value=self._ok_response(
                {'access_token': 'fresh', 'refresh_token': 'rotated', 'expires_in': 3600})) as post:
            token = self.client.get_valid_token(account)
            post.assert_called_once()

        self.assertEqual(token, 'fresh')

    def test_token_outside_the_buffer_is_left_alone(self):
        """The other side of the buffer boundary — six minutes is not urgent."""
        account = self._account(
            access_token='fine', refresh_token='r',
            token_expiry=datetime.now() + timedelta(minutes=6))
        with patch(GRAPH_POST) as post:
            self.assertEqual(self.client.get_valid_token(account), 'fine')
            post.assert_not_called()

    def test_get_valid_token_refreshes_when_expired(self):
        account = self._account(
            access_token='stale', refresh_token='old-refresh',
            token_expiry=datetime.now() - timedelta(minutes=1))
        with patch(GRAPH_POST, return_value=self._ok_response(
                {'access_token': 'fresh', 'refresh_token': 'rotated', 'expires_in': 3600})):
            token = self.client.get_valid_token(account)

        self.assertEqual(token, 'fresh')
        account.invalidate_recordset()
        self.assertEqual(account.access_token, 'fresh')
        # Microsoft rotates the refresh token on every refresh; the new one must
        # be stored or the next refresh replays a spent token.
        self.assertEqual(account.refresh_token, 'rotated')
        self.assertGreater(account.token_expiry, datetime.now())

    def test_refresh_without_a_new_refresh_token_keeps_the_old_one(self):
        """Rotation is Microsoft's norm, not its contract. When the response
        omits the refresh token the stored one must survive — clearing it would
        silently disconnect the account an hour later."""
        account = self._account(
            access_token='stale', refresh_token='still-mine',
            token_expiry=datetime.now() - timedelta(minutes=1))
        with patch(GRAPH_POST, return_value=self._ok_response(
                {'access_token': 'fresh', 'expires_in': 3600})):
            self.client.get_valid_token(account)

        account.invalidate_recordset()
        self.assertEqual(account.refresh_token, 'still-mine')

    def test_no_expiry_and_no_access_token_raises(self):
        """An account that was never authorized must say so, not return None
        and fail later with an opaque 401 from Graph."""
        account = self._account(refresh_token='r')
        with self.assertRaises(UserError) as ctx:
            self.client.get_valid_token(account)
        self.assertIn('connect', str(ctx.exception).lower())

    # ------------------------------------------------------------------ #
    # refresh_access_token failure modes
    # ------------------------------------------------------------------ #
    def test_refresh_without_a_refresh_token_raises(self):
        account = self._account(access_token='a')
        with self.assertRaises(UserError) as ctx:
            self.client.refresh_access_token(account)
        self.assertIn('reconnect', str(ctx.exception).lower())

    def test_invalid_grant_tells_the_user_to_reconnect(self):
        """A revoked refresh token, an expired one, or a changed password must
        surface as a reconnect prompt, distinct from a transient failure.

        The client also clears the dead tokens as defense-in-depth, but that
        write is best-effort: it lands only if the caller commits, and Odoo
        rolls it back when this UserError propagates out of a request. So the
        *message* is the contract worth pinning — same reasoning as the Gmail
        equivalent.
        """
        account = self._account(
            access_token='stale', refresh_token='revoked',
            token_expiry=datetime.now() - timedelta(minutes=1))
        with patch(GRAPH_POST, side_effect=self._http_error({'error': 'invalid_grant'})):
            with self.assertRaises(UserError) as ctx:
                self.client.get_valid_token(account)

        self.assertIn('reconnect', str(ctx.exception).lower())

    def test_invalid_client_also_prompts_reconnect(self):
        """Rotated app secret in Azure. Not the user's fault, but a reconnect
        is still what unblocks them once the admin fixes the registration."""
        account = self._account(
            access_token='stale', refresh_token='r',
            token_expiry=datetime.now() - timedelta(minutes=1))
        with patch(GRAPH_POST, side_effect=self._http_error({'error': 'invalid_client'})):
            with self.assertRaises(UserError) as ctx:
                self.client.get_valid_token(account)

        self.assertIn('reconnect', str(ctx.exception).lower())

    def test_transient_refresh_error_is_not_a_reconnect_prompt(self):
        """A network blip must NOT tell the user their connection is revoked —
        and must not clear tokens that are still perfectly good."""
        account = self._account(
            access_token='stale', refresh_token='still-valid',
            token_expiry=datetime.now() - timedelta(minutes=1))
        with patch(GRAPH_POST, side_effect=self._http_error(
                {'error': 'temporarily_unavailable',
                 'error_description': 'Service is temporarily unavailable'})):
            with self.assertRaises(UserError) as ctx:
                self.client.get_valid_token(account)

        self.assertNotIn('reconnect', str(ctx.exception).lower())
        account.invalidate_recordset()
        self.assertEqual(account.refresh_token, 'still-valid')

    def test_error_body_that_is_not_json_still_raises_cleanly(self):
        """Graph fronted by a proxy can return HTML. Parsing it must not turn a
        refresh failure into an unhandled ValueError."""
        resp = MagicMock()
        resp.json.side_effect = ValueError('not json')
        exc = requests.exceptions.RequestException('502 Bad Gateway')
        exc.response = resp
        account = self._account(
            access_token='stale', refresh_token='r',
            token_expiry=datetime.now() - timedelta(minutes=1))
        with patch(GRAPH_POST, side_effect=exc):
            with self.assertRaises(UserError):
                self.client.get_valid_token(account)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestGraphAuthorizationScopes(TransactionCase):
    """The scope list is the permission list, and there is only one of each.

    A token carries the scopes that were *requested*. A permission granted in
    the Azure portal and left out of this URL simply is not in the token, which
    is how the draft->send flow ran for so long on Mail.ReadWrite alone: it
    worked wherever an admin had also granted Mail.Send tenant-wide, and 403'd
    where consent was incremental. Gmail has had this test since it was
    written; Graph is the provider actually in production and had none.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = get_provider_client(cls.env, 'outlook')
        ICP = cls.env['ir.config_parameter'].sudo()
        ICP.set_param('x_pan_outlook_pro.client_id', 'test-client-id')
        ICP.set_param('x_pan_outlook_pro.tenant_id', 'test-tenant-id')

    def test_both_halves_of_the_draft_then_send_flow_are_requested(self):
        url = self.client.get_authorization_url(
            'https://odoo.test/microsoft_oauth/callback', state='abc')
        for scope in ('Mail.ReadWrite', 'Mail.ReadWrite.Shared',
                      'Mail.Send', 'Mail.Send.Shared',
                      'offline_access', 'User.Read'):
            with self.subTest(scope=scope):
                self.assertIn(scope, url)
        self.assertIn('state=abc', url)

    def test_the_documented_permissions_are_the_requested_ones(self):
        """Four places used to disagree about this list. The manifest is the
        one a buyer reads before configuring Azure, so it is the one pinned."""
        import ast
        import pathlib
        manifest_path = pathlib.Path(__file__).resolve().parent.parent / '__manifest__.py'
        described = ast.literal_eval(manifest_path.read_text())['description']
        url = self.client.get_authorization_url(
            'https://odoo.test/microsoft_oauth/callback')
        for scope in ('Mail.ReadWrite', 'Mail.ReadWrite.Shared',
                      'Mail.Send', 'Mail.Send.Shared'):
            with self.subTest(scope=scope):
                self.assertIn(scope, described)
                self.assertIn(scope, url)
