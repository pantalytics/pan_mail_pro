# -*- coding: utf-8 -*-
"""Connecting to a Pantalytics account, and trusting only what our server signed.

The server is faked at `requests.post`, but the signatures are real Ed25519:
a test that stubbed the verification would prove nothing about the one check
that makes a cached licence worth anything.
"""
import base64
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.pan_mail_pro.models import pan_mail_license
from odoo.addons.pan_mail_pro.models.pan_mail_license import canonical_json

POST = 'odoo.addons.pan_mail_pro.models.pan_mail_license.requests.post'

# What the server's Heartbeat model accepts. The module sending anything else
# would be refused there, and would be data that left the customer for nothing.
HEARTBEAT_FIELDS = {
    'db_uuid', 'module_version', 'odoo_version', 'mailboxes_connected',
    'mails_sent_24h', 'mails_received_24h', 'sync_ok', 'errors',
}


def _response(status_code, body):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = body
    return response


@tagged('post_install', '-at_install')
class TestLicense(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.signing_key = Ed25519PrivateKey.generate()
        cls.public_key = base64.b64encode(cls.signing_key.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw)).decode()
        cls.db_uuid = cls.env['ir.config_parameter'].sudo().get_param('database.uuid')
        cls.License = cls.env['pan.mail.license']

    def setUp(self):
        super().setUp()
        self.calls = []
        key_patch = patch.object(pan_mail_license, 'PUBLIC_KEY', self.public_key)
        key_patch.start()
        self.addCleanup(key_patch.stop)

    # --- a fake server ------------------------------------------------------

    def entitlement(self, **overrides):
        payload = {
            'version': 1,
            'installation_id': 7,
            'db_uuid': self.db_uuid,
            'status': 'active',
            'plan': 'standard',
            'seats_allowed': 5,
            'issued_at': '2026-09-15T12:00:00+00:00',
            'valid_until': (datetime.now(timezone.utc) + timedelta(days=14)).replace(
                microsecond=0).isoformat(),
            'latest_version': '',
            'message': '',
        }
        payload.update(overrides)
        return payload

    def signed(self, payload, key=None):
        signature = (key or self.signing_key).sign(canonical_json(payload))
        return {'entitlement': payload, 'signature': base64.b64encode(signature).decode()}

    def server(self, poll=None, heartbeat=None):
        """A POST handler answering like mail-pro-admin, recording each call."""
        def handler(url, json=None, headers=None, timeout=None):
            self.calls.append({'url': url, 'json': json, 'headers': headers or {}})
            if url.endswith('/api/v1/link/start'):
                return _response(200, {
                    'status': 'started', 'user_code': 'ABCD-EFGH',
                    'device_token': 'device-secret', 'verify_url': 'https://mailpro.test/link',
                    'expires_at': '2026-09-15T12:15:00+00:00', 'interval_seconds': 5,
                })
            if url.endswith('/api/v1/link/poll'):
                return poll or _response(200, {'status': 'linked', 'key': 'mpk_live_key'})
            if url.endswith('/api/v1/license/heartbeat'):
                return heartbeat or _response(200, self.signed(self.entitlement()))
            raise AssertionError(f'unexpected call to {url}')
        return handler

    def connected(self, **server):
        with patch(POST, side_effect=self.server(**server)):
            link = self.License.action_connect()
            link.action_check_approval()
        return link

    # --- pairing ------------------------------------------------------------

    def test_connect_shows_a_code_and_keeps_the_device_token_secret(self):
        with patch(POST, side_effect=self.server()):
            link = self.License.action_connect()
        self.assertEqual(link.status, 'pending')
        self.assertEqual(link.user_code, 'ABCD-EFGH')
        self.assertEqual(link.verify_url, 'https://mailpro.test/link')
        self.assertTrue(link.device_token_encrypted)
        self.assertNotIn('device-secret', link.device_token_encrypted)
        self.assertEqual(self.calls[0]['json']['db_uuid'], self.db_uuid)

    def test_approval_stores_the_key_encrypted_and_reports_in(self):
        link = self.connected()
        self.assertEqual(link.status, 'active')
        self.assertFalse(link.user_code)
        self.assertNotIn('mpk_live_key', link.key_encrypted)
        heartbeat = self.calls[-1]
        self.assertTrue(heartbeat['url'].endswith('/api/v1/license/heartbeat'))
        self.assertEqual(heartbeat['headers']['Authorization'], 'Bearer mpk_live_key')
        self.assertTrue(link.is_entitled())
        self.assertEqual(link.seats_allowed, 5)

    def test_checking_before_approval_changes_nothing(self):
        with patch(POST, side_effect=self.server(
                poll=_response(200, {'status': 'pending'}))):
            link = self.License.action_connect()
            action = link.action_check_approval()
        self.assertEqual(action['tag'], 'display_notification')
        self.assertEqual(link.status, 'pending')
        self.assertFalse(link.key_encrypted)

    def test_an_expired_code_starts_over(self):
        with patch(POST, side_effect=self.server(
                poll=_response(410, {'status': 'expired'}))):
            link = self.License.action_connect()
            link.action_check_approval()
        self.assertEqual(link.status, 'not_connected')
        self.assertFalse(link.user_code)

    def test_there_is_one_link_per_database(self):
        with patch(POST, side_effect=self.server()):
            first = self.License.action_connect()
            second = self.License.action_connect()
        self.assertEqual(first, second)

    def test_disconnect_forgets_the_key_and_the_answer(self):
        link = self.connected()
        link.action_disconnect()
        self.assertEqual(link.status, 'not_connected')
        self.assertFalse(link.key_encrypted)
        self.assertFalse(link.is_entitled())

    # --- trust --------------------------------------------------------------

    def test_an_answer_signed_by_somebody_else_is_not_stored(self):
        forged = self.signed(self.entitlement(seats_allowed=999),
                             key=Ed25519PrivateKey.generate())
        link = self.connected(heartbeat=_response(200, forged))
        self.assertNotEqual(link.seats_allowed, 999)
        self.assertFalse(link.is_entitled())
        self.assertTrue(link.last_error)

    def test_an_edited_answer_is_not_stored(self):
        body = self.signed(self.entitlement())
        body['entitlement']['seats_allowed'] = 999
        link = self.connected(heartbeat=_response(200, body))
        self.assertNotEqual(link.seats_allowed, 999)

    def test_an_answer_for_another_database_is_not_stored(self):
        other = self.signed(self.entitlement(db_uuid='someone-else'))
        link = self.connected(heartbeat=_response(200, other))
        self.assertFalse(link.is_entitled())

    def test_without_a_public_key_nothing_is_trusted(self):
        with patch.object(pan_mail_license, 'PUBLIC_KEY', ''):
            link = self.connected()
        self.assertFalse(link.is_entitled())

    def test_a_refused_key_drops_the_cached_answer(self):
        link = self.connected()
        with patch(POST, return_value=_response(401, {'status': 'invalid'})):
            link._heartbeat()
        self.assertEqual(link.status, 'invalid')
        self.assertFalse(link.is_entitled())

    def test_an_unreachable_server_keeps_the_cached_answer(self):
        link = self.connected()
        with patch(POST, side_effect=requests.ConnectionError('offline')):
            link._heartbeat()
        self.assertTrue(link.is_entitled())
        self.assertTrue(link.last_error)

    def test_a_cached_answer_lapses_after_valid_until(self):
        link = self.connected()
        link.valid_until = datetime.now() - timedelta(minutes=1)
        self.assertFalse(link.is_entitled())

    # --- what leaves the database ---------------------------------------------

    def test_the_heartbeat_sends_only_fields_the_server_accepts(self):
        self.connected()
        body = self.calls[-1]['json']
        self.assertLessEqual(set(body), HEARTBEAT_FIELDS)
        self.assertNotIn('@', json.dumps(body))

    def test_a_neutralized_copy_neither_connects_nor_reports(self):
        link = self.connected()
        self.calls.clear()
        self.env['ir.config_parameter'].sudo().set_param('database.is_neutralized', True)
        with patch(POST, side_effect=self.server()):
            link._heartbeat()
            with self.assertRaises(UserError):
                self.License.action_connect()
        self.assertEqual(self.calls, [])
