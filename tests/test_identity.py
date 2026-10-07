# -*- coding: utf-8 -*-
"""The identity behind a sign-in, kept rather than discarded.

Until 19.0.28 the consent callback fetched `/me`, kept the address and threw
the rest away. The rest is what tells one sign-in from another when the
address is renamed, what an administrator sees in Entra, and which directory
the sign-in belongs to -- which is the one question that, unanswered, fails at
the first send with a 404 that reads like a missing right.

Three things are asserted: every client answers the whole identity shape, the
callback stores it, and a sign-in the registration cannot serve is refused
before anything is stored.
"""
import base64
import json
from unittest.mock import MagicMock, patch

from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.pan_mail_pro.models.mail_provider_client import (
    IDENTITY_KEYS,
    PROVIDER_CLIENTS,
    decode_jwt_claims,
    get_provider_client,
    identity_shape,
)

GRAPH = 'odoo.addons.pan_mail_pro.models.providers.microsoft.graph_client'
GMAIL = 'odoo.addons.pan_mail_pro.models.providers.google.gmail_client'
GRAPH_CLIENT = f'{GRAPH}.MicrosoftGraphClient'
TENANT = '11111111-2222-3333-4444-555555555555'


def jwt(**claims):
    """An unsigned JWT with these claims: what the token endpoint hands back
    beside the access token, minus the signature nothing here checks."""
    def segment(payload):
        return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
    return f"{segment({'alg': 'none'})}.{segment(claims)}.sig"


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestIdentityShape(TransactionCase):

    def test_the_claims_of_an_id_token_are_read_without_a_signature(self):
        self.assertEqual(decode_jwt_claims(jwt(tid=TENANT, oid='o-1'))['tid'], TENANT)

    def test_anything_that_is_not_a_token_is_no_claims(self):
        for junk in (None, '', 'abc', 'a.b', 'a.!!!.c', 42):
            with self.subTest(token=junk):
                self.assertEqual(decode_jwt_claims(junk), {})

    def test_identity_shape_refuses_a_key_it_does_not_know(self):
        with self.assertRaises(ValueError):
            identity_shape(login='x')

    def test_every_client_answers_every_identity_key(self):
        """A key missing on one provider is a KeyError in the callback for that
        provider and not the others. Neutralized, so no client reaches a network."""
        self.env['ir.config_parameter'].sudo().set_param('database.is_neutralized', 'True')
        refused = MagicMock(side_effect=Exception('no network in a test'))
        with patch(f'{GRAPH}.requests.get', refused), patch(f'{GMAIL}.requests.get', refused):
            for code in PROVIDER_CLIENTS:
                with self.subTest(provider=code):
                    identity = get_provider_client(self.env, code).read_user_info('t')
                    self.assertEqual(tuple(identity), IDENTITY_KEYS)

    def test_graph_reads_the_tenant_off_the_id_token_and_the_rest_off_me(self):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            'id': 'obj-1', 'mail': 'robert@stalero.test',
            'userPrincipalName': 'robert_stalero.test#EXT#@emovr.test',
            'displayName': 'Robert van Laar'}
        with patch(f'{GRAPH}.requests.get', return_value=response):
            identity = get_provider_client(self.env, 'outlook').read_user_info(
                't', id_token=jwt(tid=TENANT, oid='obj-1'))
        self.assertEqual(identity, identity_shape(
            email='robert@stalero.test', name='Robert van Laar', provider_user_id='obj-1',
            principal_name='robert_stalero.test#EXT#@emovr.test', tenant_id=TENANT))

    def test_gmail_reads_sub_and_hd_off_the_id_token(self):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {'emailAddress': 'sales@workspace.test'}
        with patch(f'{GMAIL}.requests.get', return_value=response):
            identity = get_provider_client(self.env, 'gmail').read_user_info(
                't', id_token=jwt(sub='g-1', email='sales@workspace.test', hd='workspace.test'))
        self.assertEqual(identity, identity_shape(
            email='sales@workspace.test', provider_user_id='g-1',
            principal_name='sales@workspace.test', tenant_id='workspace.test'))


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestIdentityRefusal(TransactionCase):

    def setUp(self):
        super().setUp()
        self.env['pan.mail.provider'].create({
            'provider': 'outlook', 'client_id': 'id', 'client_secret': 's', 'tenant_id': TENANT})

    def test_a_sign_in_from_the_registrations_tenant_is_welcome(self):
        client = get_provider_client(self.env, 'outlook')
        self.assertIsNone(client.identity_refusal(identity_shape(
            email='a@b.test', tenant_id=TENANT.upper())))

    def test_a_sign_in_from_another_tenant_is_refused_by_name(self):
        client = get_provider_client(self.env, 'outlook')
        refusal = client.identity_refusal(identity_shape(email='a@b.test', tenant_id='other'))
        self.assertIn('tenant other', refusal)
        self.assertIn(TENANT, refusal)

    def test_an_identity_without_a_tenant_is_not_refused(self):
        """An older grant, or a /me read without an id_token: nothing to compare,
        nothing to refuse. The address check in `_store_tokens` still holds."""
        client = get_provider_client(self.env, 'outlook')
        self.assertIsNone(client.identity_refusal(identity_shape(email='a@b.test')))

    def test_a_multi_tenant_registration_takes_any_tenant(self):
        self.env['pan.mail.provider'].search([('provider', '=', 'outlook')]).write(
            {'tenant_id': 'organizations'})
        client = get_provider_client(self.env, 'outlook')
        self.assertIsNone(client.identity_refusal(identity_shape(
            email='a@b.test', tenant_id='other')))

    def test_a_consumer_google_account_is_refused(self):
        client = get_provider_client(self.env, 'gmail')
        refusal = client.identity_refusal(identity_shape(
            email='someone@gmail.com', provider_user_id='g-1'))
        self.assertIn('personal Google account', refusal)
        self.assertIsNone(client.identity_refusal(identity_shape(
            email='sales@workspace.test', provider_user_id='g-1', tenant_id='workspace.test')))
        # No id_token at all: nothing says it is a consumer account.
        self.assertIsNone(client.identity_refusal(identity_shape(email='sales@workspace.test')))

    def test_imap_refuses_nobody(self):
        self.assertIsNone(get_provider_client(self.env, 'imap').identity_refusal(identity_shape()))


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestIdentityStored(TransactionCase):

    def setUp(self):
        super().setUp()
        self.user = self.env['res.users'].create({
            'name': 'Nora', 'login': 'nora@company.test', 'email': 'nora@company.test'})

    def test_store_tokens_keeps_what_the_provider_said(self):
        account = self.env['pan.mail.account']._store_tokens(
            'outlook', self.user, 'nora@company.test', 'at', 'rt', False,
            identity=identity_shape(
                email='nora@company.test', name='Nora Employee', provider_user_id='obj-1',
                principal_name='nora@company.test', tenant_id=TENANT),
            scopes='openid Mail.Send MailboxSettings.Read')
        self.assertEqual(account.display_name, 'Nora Employee')
        self.assertEqual(account.provider_user_id, 'obj-1')
        self.assertEqual(account.tenant_id, TENANT)
        self.assertTrue(account.connected_date)
        self.assertTrue(account.verified_date)
        self.assertTrue(account.has_scope('MailboxSettings.Read'))
        self.assertFalse(account.has_scope('User.ReadBasic.All'))

    def test_a_grant_that_recorded_no_scopes_has_none(self):
        """An older grant: the permission may well be there, but a probe that
        assumes so cannot tell consent from rights when it meets a 403."""
        account = self.env['pan.mail.account']._store_tokens(
            'outlook', self.user, 'nora@company.test', 'at', 'rt', False)
        self.assertFalse(account.has_scope('MailboxSettings.Read'))

    def test_google_scopes_are_urls_and_match_on_their_last_segment(self):
        account = self.env['pan.mail.account']._store_tokens(
            'gmail', self.user, 'nora@company.test', 'at', 'rt', False,
            scopes='https://www.googleapis.com/auth/gmail.modify openid')
        self.assertTrue(account.has_scope('gmail.modify'))

    def test_the_label_names_the_person(self):
        account = self.env['pan.mail.account']._store_tokens(
            'outlook', self.user, 'nora@company.test', 'at', 'rt', False,
            identity=identity_shape(email='nora@company.test', name='Nora Employee'))
        self.assertEqual(account._identity_label({}), 'Nora Employee (nora@company.test)')


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestIdentityCallback(HttpCase):
    """The callback refuses a foreign tenant before it stores anything."""

    def setUp(self):
        super().setUp()
        self.env['pan.mail.domain'].set_domains(['company.test'])
        self.env['pan.mail.provider'].create({
            'provider': 'outlook', 'client_id': 'id', 'client_secret': 'secret',
            'tenant_id': TENANT})
        self.user = self.env['res.users'].create({
            'name': 'Nora Employee', 'login': 'nora@company.test',
            'password': 'nora@company.test', 'email': 'nora@company.test',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        self.authenticate('nora@company.test', 'nora@company.test')
        self.user.sudo().x_pan_mail_oauth_state = 'nonce-123'

    def _grant(self, tenant):
        tokens = {'access_token': 'at', 'refresh_token': 'rt',
                  'token_expiry': '2030-01-01 00:00:00',
                  'id_token': jwt(tid=tenant, oid='obj-1'),
                  'scope': 'openid Mail.Send MailboxSettings.Read User.ReadBasic.All'}
        me = MagicMock()
        me.raise_for_status.return_value = None
        me.json.return_value = {'id': 'obj-1', 'mail': 'nora@company.test',
                                'userPrincipalName': 'nora@company.test',
                                'displayName': 'Nora Employee'}
        return (patch(f'{GRAPH_CLIENT}._exchange_code_for_tokens', return_value=tokens),
                patch(f'{GRAPH}.requests.get', return_value=me))

    def _accounts(self):
        return self.env['pan.mail.account'].sudo().search([('user_id', '=', self.user.id)])

    def test_the_callback_stores_the_identity_and_the_scopes(self):
        exchange, me = self._grant(TENANT)
        with exchange, me:
            response = self.url_open('/microsoft_oauth/callback?code=c&state=nonce-123')
        self.assertIn('Mailbox Connected', response.text)
        account = self._accounts()
        self.assertEqual(account.display_name, 'Nora Employee')
        self.assertEqual(account.tenant_id, TENANT)
        self.assertEqual(account.provider_user_id, 'obj-1')
        self.assertTrue(account.has_scope('User.ReadBasic.All'))

    def test_a_foreign_tenant_is_refused_and_nothing_is_stored(self):
        exchange, me = self._grant('99999999-0000-0000-0000-000000000000')
        with exchange, me:
            response = self.url_open('/microsoft_oauth/callback?code=c&state=nonce-123')
        self.assertIn('Connection Failed', response.text)
        self.assertIn('tenant 99999999', response.text)
        self.assertFalse(self._accounts())
        self.assertTrue(self.env['pan.mail.error'].sudo().search(
            [('code', '=', 'oauth.tenant_mismatch')]))
