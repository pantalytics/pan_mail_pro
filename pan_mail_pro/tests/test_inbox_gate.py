# -*- coding: utf-8 -*-
"""The Inbox's gate connects, and Approve lands back on it (#380).

The gate's button used to open Settings, where a second Connect button and
"Not connected" read as if the first click had failed. It runs the pairing
itself now, opens the approval page in the same tab, and the way back from
that page (`/mail_pro/pantalytics/return`) lands on the screen Connect was
pressed on, with the key collected. The Inbox collects too, on load, for
whoever reached it by another road.

The server is faked at `requests.post` the way `test_license.py` fakes it,
signatures included: the gate opens on a verified answer or not at all.
"""
import base64
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

from odoo.addons.pan_mail_pro.models import pan_mail_license
from odoo.addons.pan_mail_pro.models.res_users import INBOX_URL, SETTINGS_URL

# The module, not its class: a TestCase imported by name is collected here a
# second time and its whole suite runs twice.
from . import test_license
from .test_license import POST, _response

# The real `sync_allowed()`, not the yes `tests/connected.py` gives every
# other test: the gate is the thing under test.
REAL = {'pan_mail_pro_real_gate': True}


class _FakePlatform:
    """The pairing server of `test_license.TestLicense`, borrowed: the same
    answers, the same real Ed25519 signatures."""

    entitlement = test_license.TestLicense.entitlement
    signed = test_license.TestLicense.signed
    server = test_license.TestLicense.server

    @classmethod
    def _keys(cls):
        cls.signing_key = Ed25519PrivateKey.generate()
        cls.public_key = base64.b64encode(cls.signing_key.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw)).decode()
        cls.db_uuid = cls.env['ir.config_parameter'].sudo().get_param('database.uuid')

    def _trust(self):
        self.calls = []
        key_patch = patch.object(pan_mail_license, 'PUBLIC_KEY', self.public_key)
        key_patch.start()
        self.addCleanup(key_patch.stop)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestInboxGate(_FakePlatform, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._keys()
        cls.License = cls.env['pan.mail.license'].with_context(**REAL)

    def setUp(self):
        super().setUp()
        self._trust()

    def test_connect_opens_the_approval_page_in_this_tab(self):
        """Not a new tab: one opened after the call answers is no longer the
        click, and Brave and Safari block it."""
        with patch(POST, side_effect=self.server()):
            action = self.License.action_connect_open()
        self.assertEqual(action, {
            'type': 'ir.actions.act_url', 'target': 'self',
            'url': 'https://mailpro.test/link?code=ABCD-EFGH'})
        self.assertEqual(self.License.current().status, 'pending')

    def test_an_unconnected_instance_shows_the_gate_and_connect_pairs(self):
        self.assertEqual(self.License.inbox_gate(), {'connected': False, 'pairs': True})

    def test_an_approved_pairing_is_collected_when_the_inbox_loads(self):
        """Whoever approved and reached the Inbox some other way than the
        link back finds it open: no Check again, nothing to press."""
        with patch(POST, side_effect=self.server()):
            self.License.action_connect()
            gate = self.License.inbox_gate()
        link = self.License.current()
        self.assertEqual(gate, {'connected': True, 'pairs': False})
        self.assertEqual(link.status, 'active')
        self.assertTrue(link.key_encrypted)
        self.assertEqual(link.connected_user_id, self.env.user)

    def test_before_approval_the_gate_stays_and_the_pairing_waits(self):
        with patch(POST, side_effect=self.server(
                poll=_response(200, {'status': 'pending'}))):
            self.License.action_connect()
            gate = self.License.inbox_gate()
        self.assertEqual(gate, {'connected': False, 'pairs': True})
        self.assertEqual(self.License.current().status, 'pending')

    def test_an_expired_pairing_is_dropped_and_connect_starts_a_new_one(self):
        with patch(POST, side_effect=self.server(
                poll=_response(410, {'status': 'expired'}))):
            self.License.action_connect()
            gate = self.License.inbox_gate()
        self.assertEqual(gate, {'connected': False, 'pairs': True})
        self.assertEqual(self.License.current().status, 'not_connected')

    def test_a_key_the_answer_said_no_to_is_not_paired_again(self):
        """No plan on the workspace: a second pairing gets the same answer.
        The gate's button opens Settings, which says what to do."""
        with patch(POST, side_effect=self.server(heartbeat=_response(
                200, self.signed(self.entitlement(status='canceled'))))):
            self.License.action_connect()
            self.License.current().action_check_approval()
        self.assertEqual(self.License.current().status, 'canceled')
        with patch(POST, side_effect=AssertionError('nothing to ask')):
            gate = self.License.inbox_gate()
        self.assertEqual(gate, {'connected': False, 'pairs': False})

    def test_a_dead_key_is_paired_again(self):
        self.License.sudo().create({'status': 'revoked', 'key_encrypted': 'gone'})
        self.assertEqual(self.License.inbox_gate(), {'connected': False, 'pairs': True})

    def test_a_plain_user_on_the_gate_collects_nothing(self):
        """Connecting is an administrator's; a plain user who opens the
        Inbox while a pairing waits does not poll on their behalf."""
        with patch(POST, side_effect=self.server()):
            self.License.action_connect()
        plain = new_test_user(self.env, login='gate_plain', groups='base.group_user')
        calls = len(self.calls)
        with patch(POST, side_effect=self.server()):
            gate = self.License.with_user(plain).inbox_gate()
        self.assertFalse(gate['connected'])
        self.assertEqual(len(self.calls), calls)
        self.assertEqual(self.License.current().status, 'pending')

    def test_only_an_administrator_may_press_connect(self):
        plain = new_test_user(self.env, login='gate_plain', groups='base.group_user')
        with patch(POST, side_effect=self.server()), self.assertRaises(AccessError):
            self.License.with_user(plain).action_connect_open()
        self.assertFalse(self.calls)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestBackFromTheApprovalPage(_FakePlatform, HttpCase):
    """Approve sends the browser to one fixed path on this Odoo. While setup
    is not finished it lands on the checklist; after, where this session
    remembered at Connect. Every case but the first is a finished setup: a
    reconnect."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._keys()

    def setUp(self):
        super().setUp()
        ready = patch.object(type(self.env['pan.mail.setup']), 'is_ready', return_value=True)
        ready.start()
        self.addCleanup(ready.stop)
        self._trust()
        self.authenticate('admin', 'admin')

    def _connect(self, context=None, headers=None):
        with patch(POST, side_effect=self.server()):
            return self.make_jsonrpc_request(
                '/web/dataset/call_kw/pan.mail.license/action_connect_open', {
                    'model': 'pan.mail.license', 'method': 'action_connect_open',
                    'args': [], 'kwargs': {'context': context or {}},
                }, headers=headers)

    def _back(self):
        with patch(POST, side_effect=self.server()):
            response = self.url_open('/mail_pro/pantalytics/return', allow_redirects=False)
        self.assertIn(response.status_code, (302, 303))
        return response.headers['Location']

    def test_while_setup_is_not_done_it_lands_on_the_checklist(self):
        """Connect is step 1; the provider is step 2, on Settings. The Inbox
        would only say "No mailbox yet" (Rutger's test, 6 Nov)."""
        self._connect({'pan_mail_back': INBOX_URL})
        with patch.object(type(self.env['pan.mail.setup']), 'is_ready', return_value=False):
            location = self._back()
        self.assertTrue(location.endswith(SETTINGS_URL), location)
        self.assertEqual(self.env['pan.mail.license'].current().status, 'active')

    def test_connect_on_the_gate_lands_back_in_the_inbox_connected(self):
        action = self._connect({'pan_mail_back': INBOX_URL})
        self.assertEqual(action['url'], 'https://mailpro.test/link?code=ABCD-EFGH')
        location = self._back()
        self.assertTrue(location.endswith(INBOX_URL), location)
        link = self.env['pan.mail.license'].current()
        self.assertEqual(link.status, 'active')
        self.assertGreater(link.valid_until, fields.Datetime.now())

    def test_connect_in_settings_lands_back_in_settings(self):
        """Settings' button says nothing; its Referer is Settings."""
        self._connect(headers={'Referer': self.base_url() + '/odoo/settings'})
        self.assertTrue(self._back().endswith(SETTINGS_URL))

    def test_without_a_word_from_the_caller_the_referer_is_where_it_lands(self):
        self._connect(headers={'Referer': self.base_url() + '/odoo/action-123?debug=1'})
        self.assertTrue(self._back().endswith('/odoo/action-123?debug=1'))

    def test_the_way_back_is_used_once(self):
        self._connect({'pan_mail_back': INBOX_URL})
        self._back()
        self.assertTrue(self._back().endswith(SETTINGS_URL))

    def test_a_way_back_to_another_host_lands_in_settings(self):
        self._connect({'pan_mail_back': '//evil.example/odoo'})
        location = self._back()
        self.assertNotIn('evil.example', location)
        self.assertTrue(location.endswith(SETTINGS_URL))
