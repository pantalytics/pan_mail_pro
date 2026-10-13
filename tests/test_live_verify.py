# -*- coding: utf-8 -*-
"""Confirm it's you: the live read wants the provider's word, per session.

An Odoo session as the owner is not proof of the owner. A hoster's "log in
as", an administrator who resets the password, and the owner all hold one,
and Odoo cannot tell them apart. So before the live read answers, the
provider has to have said, in *this* session, that the person at the
keyboard is the sign-in behind the mailbox: a Microsoft or Google round trip
compared on the stable id, or one IMAP login with a password typed rather
than stored. The proof lives in `request.session`, a file and not a row.

Two halves. The gate (`TransactionCase`): what a session without the word
gets, what the proof has to name, and the password round for a provider with
no sign-in screen. The round trip (`HttpCase`): where the start sends the
browser, what the callback writes on a yes, and the three ways it says no.
"""
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import odoo.http
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

from .common import proven_session

CLIENT = 'imap.smtp.client'
GRAPH = 'odoo.addons.pan_mail_pro.models.providers.microsoft.graph_client.MicrosoftGraphClient'
VERIFIED = 'pan_mail_verified'
PENDING = 'pan_mail_verify'


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestLiveVerifyGate(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['pan.mail.domain'].set_domains(['company.test'])
        cls.Conversation = cls.env['pan.mail.conversation']
        cls.owner = new_test_user(cls.env, login='lv_owner', groups='base.group_user',
                                  email='rutger@company.test')
        cls.colleague = new_test_user(cls.env, login='lv_colleague', groups='base.group_user',
                                      email='sanne@company.test')
        imap = {
            'imap_host': 'imap.company.test', 'imap_port': 993, 'imap_security': 'ssl',
            'smtp_host': 'smtp.company.test', 'smtp_port': 465, 'smtp_security': 'ssl',
            'password': 'hunter2',
        }
        cls.account = cls.env['pan.mail.account'].create({
            'email': 'rutger@company.test', 'provider': 'imap', 'user_id': cls.owner.id, **imap})
        cls.mailbox = cls.env['pan.mail.mailbox'].create({
            'email': 'rutger@company.test', 'provider': 'imap',
            'mailbox_type': 'personal', 'owner_user_id': cls.owner.id})
        cls.their_account = cls.env['pan.mail.account'].create({
            'email': 'sanne@company.test', 'provider': 'imap', 'user_id': cls.colleague.id, **imap})
        cls.env['pan.mail.mailbox'].create({
            'email': 'sanne@company.test', 'provider': 'imap',
            'mailbox_type': 'personal', 'owner_user_id': cls.colleague.id})

    def _as_owner(self):
        return self.Conversation.with_user(self.owner)

    def _serving(self):
        return patch.object(type(self.env[CLIENT]), 'search_messages', return_value=[])

    # ------------------------------------------------------------------ the gate

    def test_the_owner_s_session_alone_is_not_enough(self):
        """The case this exists for: a session as the owner that the owner
        did not open. No request at all is the same answer."""
        with self.assertRaises(AccessError):
            self._as_owner().live_messages(self.mailbox.id)
        with proven_session(self.env), self.assertRaises(AccessError):
            self._as_owner().live_messages(self.mailbox.id)

    def test_every_live_method_is_behind_the_gate(self):
        """One check in `_own_mailbox`, so a method that reads, marks or
        files through it is refused with it."""
        calls = (
            ('live_messages', (self.mailbox.id,)),
            ('read_live_message', (self.mailbox.id, 'AAA')),
            ('import_live_message', (self.mailbox.id, 'AAA')),
            ('live_mark', (self.mailbox.id, 'AAA', 'read')),
        )
        with proven_session(self.env):
            for name, args in calls:
                with self.subTest(method=name), self.assertRaises(AccessError):
                    getattr(self._as_owner(), name)(*args)

    def test_the_proof_opens_the_mailbox(self):
        with proven_session(self.env, accounts=[self.account], uid=self.owner.id), \
                self._serving():
            result = self._as_owner().live_messages(self.mailbox.id)
        self.assertTrue(result['connected'])

    def test_the_proof_names_the_account(self):
        """A session that proved the colleague's sign-in has proved nothing
        about this mailbox."""
        with proven_session(self.env, accounts=[self.their_account], uid=self.owner.id), \
                self.assertRaises(AccessError):
            self._as_owner().live_messages(self.mailbox.id)

    def test_the_proof_names_the_user(self):
        """Written for one user, read by another: no. Belt and braces for a
        session that changes hands without rotating."""
        with proven_session(self.env, accounts=[self.account], uid=self.colleague.id), \
                self.assertRaises(AccessError):
            self._as_owner().live_messages(self.mailbox.id)

    def test_ownership_still_comes_first(self):
        """The proof does not widen ownership: a colleague with a proof for
        their own account reads their mailbox and not this one."""
        with proven_session(self.env, accounts=[self.their_account], uid=self.colleague.id), \
                self.assertRaises(AccessError):
            self.Conversation.with_user(self.colleague).live_messages(self.mailbox.id)

    def test_the_list_says_whether_the_session_is_proven(self):
        """What the Inbox draws the line from, and what it asks with."""
        with proven_session(self.env):
            before = self._as_owner().live_mailboxes()
        with proven_session(self.env, accounts=[self.account], uid=self.owner.id):
            after = self._as_owner().live_mailboxes()
        self.assertEqual([m['verified'] for m in before], [False])
        self.assertEqual([m['verified'] for m in after], [True])
        self.assertEqual(before[0]['uses_oauth'], False)
        self.assertEqual(before[0]['provider_label'], 'IMAP / SMTP')

    def test_imported_mail_is_not_behind_it(self):
        """What Odoo holds was readable to the same people before; the gate
        is on the live read only."""
        rows = self._as_owner().search_conversations(mailbox_id=self.mailbox.id)
        self.assertIsInstance(rows, list)

    # ------------------------------------------------------------------ the password

    def _server(self, accepts):
        """An IMAP server that takes `accepts` and nothing else."""
        def connect(_client, account, password=None):
            if password == accepts:
                return MagicMock()
            raise UserError('LOGIN failed')
        return patch.object(type(self.env[CLIENT]), '_connect', connect)

    def test_the_password_the_server_accepts_is_the_proof(self):
        with proven_session(self.env) as session, self._server('hunter2'):
            ok = self._as_owner().verify_mailbox_password(self.mailbox.id, 'hunter2')
            self.assertTrue(ok)
            self.assertEqual(session[VERIFIED]['uid'], self.owner.id)
            self.assertIn(str(self.account.id), session[VERIFIED]['accounts'])
            with self._serving():
                self.assertTrue(self._as_owner().live_messages(self.mailbox.id)['connected'])

    def test_a_wrong_password_proves_nothing(self):
        with proven_session(self.env) as session, self._server('hunter2'):
            self.assertFalse(
                self._as_owner().verify_mailbox_password(self.mailbox.id, 'wrong'))
            self.assertNotIn(VERIFIED, session)
            with self.assertRaises(AccessError):
                self._as_owner().live_messages(self.mailbox.id)

    def test_the_stored_password_is_not_the_answer(self):
        """The server is the judge. What Odoo stores is what a stolen session
        already has, so typing it back must prove nothing unless the server
        takes it -- and here the server takes something else."""
        with proven_session(self.env) as session, self._server('rotated-since'):
            self.assertFalse(
                self._as_owner().verify_mailbox_password(self.mailbox.id, 'hunter2'))
            self.assertNotIn(VERIFIED, session)

    def test_an_empty_password_is_not_tried(self):
        with proven_session(self.env), self._server(''):
            self.assertFalse(
                self._as_owner().verify_mailbox_password(self.mailbox.id, ''))

    def test_the_password_round_is_the_owner_s_only(self):
        with proven_session(self.env), self._server('hunter2'), self.assertRaises(AccessError):
            self.Conversation.with_user(self.colleague).verify_mailbox_password(
                self.mailbox.id, 'hunter2')

    def test_an_oauth_mailbox_is_confirmed_by_its_provider(self):
        """No password to type: its proof comes back through the callback."""
        self.env['pan.mail.account'].create({
            'email': 'rutger-o@company.test', 'provider': 'outlook',
            'user_id': self.owner.id, 'refresh_token': 'rt', 'access_token': 'at'})
        mailbox = self.env['pan.mail.mailbox'].create({
            'email': 'rutger-o@company.test', 'provider': 'outlook',
            'mailbox_type': 'personal', 'owner_user_id': self.owner.id})
        with proven_session(self.env), self.assertRaises(UserError):
            self._as_owner().verify_mailbox_password(mailbox.id, 'anything')


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestVerifyRoundTrip(HttpCase):
    """The sign-in round: `/mail_pro/connect?verify=1` out, the callback back."""

    def setUp(self):
        super().setUp()
        self.env['pan.mail.domain'].set_domains(['company.test'])
        self.env['pan.mail.provider'].create({
            'provider': 'outlook', 'client_id': 'id', 'client_secret': 'secret',
            'tenant_id': '11111111-2222-3333-4444-555555555555'})
        self.user = self.env['res.users'].create({
            'name': 'Nora Employee', 'login': 'nora@company.test',
            'password': 'nora@company.test', 'email': 'nora@company.test',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        self.account = self.env['pan.mail.account'].create({
            'email': 'nora@company.test', 'provider': 'outlook', 'user_id': self.user.id,
            'refresh_token': 'rt-before', 'access_token': 'at-before',
            'provider_user_id': 'oid-nora'})
        self.mailbox = self.env['pan.mail.mailbox'].create({
            'email': 'nora@company.test', 'provider': 'outlook',
            'owner_user_id': self.user.id})
        self.assertEqual(self.mailbox.mailbox_type, 'personal')
        self.authenticate('nora@company.test', 'nora@company.test')

    # ------------------------------------------------------------------ helpers

    def _stored(self):
        """The session as the server saved it after the last request."""
        return odoo.http.root.session_store.get(self.session.sid)

    def _arm(self, silent=True, back='/odoo/action-1#inbox'):
        """A round trip in flight, as `_start_verify` leaves it."""
        self.session[PENDING] = {
            'state': 'nonce-v', 'mailbox_id': self.mailbox.id, 'back': back,
            'silent': silent, 'provider': 'outlook'}
        odoo.http.root.session_store.save(self.session)

    def _start(self, **params):
        query = '&'.join(f'{key}={value}' for key, value in params.items())
        return self.url_open(f'/mail_pro/connect?verify=1&{query}', allow_redirects=False)

    def _callback(self, **params):
        query = '&'.join(f'{key}={value}' for key, value in params.items())
        return self.url_open(f'/microsoft_oauth/callback?{query}', allow_redirects=False)

    def _signed_in_as(self, oid, email):
        tokens = {'access_token': 'at-round', 'refresh_token': 'rt-round',
                  'token_expiry': '2030-01-01 00:00:00'}
        identity = {'email': email, 'name': None, 'provider_user_id': oid,
                    'principal_name': email, 'tenant_id': None}
        return (patch(f'{GRAPH}._exchange_code_for_tokens', return_value=tokens),
                patch(f'{GRAPH}.read_user_info', return_value=identity))

    # ------------------------------------------------------------------ the start

    def test_the_start_goes_to_the_provider_with_the_address_filled_in(self):
        response = self._start(mailbox_id=self.mailbox.id, back='/odoo/action-1%23inbox')
        self.assertIn(response.status_code, (302, 303))
        url = urlparse(response.headers['Location'])
        query = parse_qs(url.query)
        self.assertEqual(url.netloc, 'login.microsoftonline.com')
        self.assertEqual(query['login_hint'], ['nora@company.test'])
        self.assertEqual(query['prompt'], ['none'], 'the first try is silent')
        pending = self._stored().get(PENDING)
        self.assertEqual(query['state'], [pending['state']])
        self.assertEqual(pending['mailbox_id'], self.mailbox.id)
        self.assertEqual(pending['back'], '/odoo/action-1#inbox')
        self.assertTrue(pending['silent'])

    def test_the_way_back_never_leaves_this_odoo(self):
        self._start(mailbox_id=self.mailbox.id, back='https://evil.test/x')
        self.assertEqual(self._stored()[PENDING]['back'],
                         '/odoo/action-pan_mail_pro.action_pan_mail_conversation')
        self._start(mailbox_id=self.mailbox.id, back='//evil.test/x')
        self.assertEqual(self._stored()[PENDING]['back'],
                         '/odoo/action-pan_mail_pro.action_pan_mail_conversation')

    def test_a_colleague_s_mailbox_cannot_be_started(self):
        other = self.env['res.users'].create({
            'name': 'Sanne', 'login': 'sanne@company.test', 'email': 'sanne@company.test',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        self.env['pan.mail.account'].create({
            'email': 'sanne@company.test', 'provider': 'outlook', 'user_id': other.id,
            'refresh_token': 'rt', 'access_token': 'at'})
        theirs = self.env['pan.mail.mailbox'].create({
            'email': 'sanne@company.test', 'provider': 'outlook', 'owner_user_id': other.id})
        response = self._start(mailbox_id=theirs.id)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(PENDING, self._stored())

    # ------------------------------------------------------------------ the callback

    def test_a_matching_sign_in_is_the_proof_and_nothing_else_changes(self):
        self._arm()
        exchange, me = self._signed_in_as('oid-nora', 'nora@company.test')
        with exchange, me:
            response = self._callback(code='authcode', state='nonce-v')
        self.assertIn(response.status_code, (302, 303))
        self.assertTrue(response.headers['Location'].endswith('/odoo/action-1#inbox'))
        stored = self._stored()
        self.assertEqual(stored[VERIFIED]['uid'], self.user.id)
        self.assertIn(str(self.account.id), stored[VERIFIED]['accounts'])
        self.assertNotIn(PENDING, stored, 'the nonce is spent')
        self.account.invalidate_recordset()
        self.assertEqual(self.account.access_token, 'at-before',
                         'the tokens of a confirm round are dropped')
        self.assertEqual(self.account.refresh_token, 'rt-before')

    def test_another_sign_in_is_refused_by_name(self):
        """The administrator with Nora's Odoo session and their own Microsoft
        account: same tenant, same consent screen, another stable id."""
        self._arm()
        exchange, me = self._signed_in_as('oid-robert', 'robert@company.test')
        with exchange, me:
            response = self._callback(code='authcode', state='nonce-v')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Not confirmed', response.text)
        self.assertIn('robert@company.test', response.text)
        self.assertIn('nora@company.test', response.text)
        self.assertNotIn(VERIFIED, self._stored())

    def test_the_same_address_with_another_id_is_another_account(self):
        """The stable id decides when both sides have one; an address can be
        reassigned in a directory, an object id cannot."""
        self._arm()
        exchange, me = self._signed_in_as('oid-someone-else', 'nora@company.test')
        with exchange, me:
            response = self._callback(code='authcode', state='nonce-v')
        self.assertIn('Not confirmed', response.text)
        self.assertNotIn(VERIFIED, self._stored())

    def test_a_grant_without_a_stored_id_is_compared_on_the_address(self):
        """A grant from before the id was kept (19.0.28) still gets its owner
        through, on the one thing it has."""
        self.account.sudo().write({'provider_user_id': False})
        self._arm()
        exchange, me = self._signed_in_as('oid-nora', 'Nora@Company.test')
        with exchange, me:
            response = self._callback(code='authcode', state='nonce-v')
        self.assertIn(response.status_code, (302, 303))
        self.assertIn(VERIFIED, self._stored())

    def test_a_silent_refusal_tries_again_with_a_screen(self):
        """`prompt=none` fails rather than asks when the browser is not signed
        in at the provider; the second try asks."""
        self._arm(silent=True)
        response = self._callback(error='login_required', state='nonce-v')
        self.assertIn(response.status_code, (302, 303))
        url = urlparse(response.headers['Location'])
        query = parse_qs(url.query)
        self.assertEqual(url.netloc, 'login.microsoftonline.com')
        self.assertEqual(query['login_hint'], ['nora@company.test'])
        self.assertNotIn('prompt', query)
        pending = self._stored()[PENDING]
        self.assertFalse(pending['silent'])
        self.assertNotEqual(pending['state'], 'nonce-v', 'a fresh nonce for the second try')
        self.assertNotIn(VERIFIED, self._stored())

    def test_a_refusal_on_the_second_try_is_the_answer(self):
        self._arm(silent=False)
        response = self._callback(error='access_denied', state='nonce-v')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Not confirmed', response.text)
        self.assertNotIn(VERIFIED, self._stored())
        self.assertNotIn(PENDING, self._stored())

    def test_the_nonce_is_good_once(self):
        self._arm()
        exchange, me = self._signed_in_as('oid-nora', 'nora@company.test')
        with exchange, me:
            self._callback(code='authcode', state='nonce-v')
            replay = self._callback(code='authcode', state='nonce-v')
        # No round in flight: the callback is an ordinary connect now, and an
        # ordinary connect with a nonce the user row never issued is refused.
        self.assertIn('Connection Failed', replay.text)

    def test_a_pending_round_does_not_catch_an_ordinary_connect(self):
        """Two nonces, two flows. A connect's nonce lives on the user row
        and a connect with it stores a grant, round in flight or not."""
        self._arm()
        self.user.sudo().x_pan_mail_oauth_state = 'nonce-connect'
        exchange, me = self._signed_in_as('oid-nora', 'nora@company.test')
        with exchange, me:
            response = self._callback(code='authcode', state='nonce-connect')
        self.assertIn('Connected', response.text)
        self.account.invalidate_recordset()
        self.assertEqual(self.account.access_token, 'at-round')
        self.assertNotIn(VERIFIED, self._stored(), 'a connect is not a confirm')
