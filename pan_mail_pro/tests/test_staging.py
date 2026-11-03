# -*- coding: utf-8 -*-
"""A copy connects as staging, and stays a copy (pan_mail_pro#384).

The platform half is odoo-mcp-pro-admin#594: `/api/v1/link/start` takes
`staging: true` for Mail Pro, the approval page asks which production the copy
is of, the heartbeat takes an optional `staging` boolean, and the signed
answer to a copy carries `staging: true` with no monthly mails. Here: a copy
may Connect, Check approval and report in, says `staging: true` on the start
call and every heartbeat, and a production says nothing new. An answer that
says staging keeps the database a copy, neutralized or not, so connecting a
copy never opens a provider.

The server is faked at `requests.post` the way `test_license.py` fakes it,
signatures included.
"""
import os
import re
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.pan_mail_pro.models import encryption_utils
from odoo.addons.pan_mail_pro.models.neutralization import database_is_copy

from .test_inbox_gate import _FakePlatform
from .test_license import POST, _response

REAL = {'pan_mail_pro_real_gate': True}
START = '/api/v1/link/start'
HEARTBEAT = '/api/v1/license/heartbeat'


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestStaging(_FakePlatform, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._keys()
        cls.License = cls.env['pan.mail.license'].with_context(**REAL)

    def setUp(self):
        super().setUp()
        self._trust()

    # --- helpers --------------------------------------------------------------

    def neutralize(self):
        self.env['ir.config_parameter'].sudo().set_param('database.is_neutralized', 'True')

    def staging_server(self, **server):
        """The platform as it answers a copy linked as staging."""
        return self.server(heartbeat=_response(200, self.signed(self.entitlement(
            staging=True, mails_included=0, mails_used_month=0))), **server)

    def sent(self, path):
        return [call['json'] for call in self.calls if call['url'].endswith(path)]

    # --- what a copy sends ------------------------------------------------------

    def test_connect_on_a_copy_says_staging(self):
        self.neutralize()
        with patch(POST, side_effect=self.staging_server()):
            link = self.License.action_connect()
        self.assertEqual(link.status, 'pending')
        [start] = self.sent(START)
        self.assertIs(start['staging'], True)
        self.assertEqual(start['db_uuid'], self.db_uuid)

    def test_connect_on_a_production_says_nothing_new(self):
        with patch(POST, side_effect=self.server()):
            self.License.action_connect()
        [start] = self.sent(START)
        self.assertNotIn('staging', start)
        self.assertEqual(set(start), {'db_uuid', 'odoo_url', 'module_version'})

    def test_a_copy_collects_its_key_and_reports_in_as_staging(self):
        """Check approval reads the device token past the decryption gate, the
        heartbeat reads the key the same way, and both go out."""
        self.neutralize()
        with patch(POST, side_effect=self.staging_server()):
            link = self.License.action_connect()
            link.action_check_approval()
        self.assertEqual(link.status, 'active')
        self.assertTrue(link.is_entitled())
        self.assertTrue(link.sync_allowed())
        [poll] = self.sent('/api/v1/link/poll')
        self.assertEqual(poll, {'device_token': 'device-secret'})
        [heartbeat] = self.sent(HEARTBEAT)
        self.assertIs(heartbeat['staging'], True)
        self.assertEqual(self.calls[-1]['headers']['Authorization'], 'Bearer mpk_live_key')
        self.assertIn('"staging": true', link.entitlement_json)

    def test_every_heartbeat_from_a_copy_says_staging(self):
        self.neutralize()
        with patch(POST, side_effect=self.staging_server()):
            link = self.License.action_connect()
            link.action_check_approval()
            link._heartbeat()
            self.License._cron_heartbeat()
        beats = self.sent(HEARTBEAT)
        self.assertEqual(len(beats), 3)
        self.assertTrue(all(beat['staging'] is True for beat in beats))

    def test_a_production_heartbeat_carries_no_staging(self):
        with patch(POST, side_effect=self.server()):
            link = self.License.action_connect()
            link.action_check_approval()
            link._heartbeat()
        beats = self.sent(HEARTBEAT)
        self.assertEqual(len(beats), 2)
        for beat in beats:
            self.assertNotIn('staging', beat)
        self.assertFalse(database_is_copy(self.env))

    def test_staging_is_a_boolean_and_nothing_else(self):
        """The heartbeat is the privacy boundary: one more flag, no name, no
        address, no production label."""
        self.neutralize()
        with patch(POST, side_effect=self.staging_server(poll=_response(200, {
                'status': 'linked', 'key': 'mpk_live_key',
                'staging_of': 'acme.odoo.com'}))):
            link = self.License.action_connect()
            link.action_check_approval()
        [heartbeat] = self.sent(HEARTBEAT)
        self.assertIs(heartbeat['staging'], True)
        self.assertNotIn('acme', repr(heartbeat))

    def test_the_fetch_cron_on_a_copy_asks_again_and_reads_nothing(self):
        """A copy's answer lapses like any other. The fetch cron asks again on
        a copy before it stops at the copy's return; it never opens a mailbox."""
        self.neutralize()
        with patch(POST, side_effect=self.staging_server()):
            link = self.License.action_connect()
            link.action_check_approval()
        link.write({'valid_until': fields.Datetime.now(),
                    'last_check': fields.Datetime.subtract(fields.Datetime.now(), hours=1)})
        self.calls.clear()
        with patch(POST, side_effect=self.staging_server()), self.assertLogs(
                'odoo.addons.pan_mail_pro.models.pan_mail_fetcher', 'INFO') as logged:
            self.env['pan.mail.fetcher']._cron_fetch_incoming_mail()
        self.assertIn('Database is a copy - skipping sync', '\n'.join(logged.output))
        [heartbeat] = self.sent(HEARTBEAT)
        self.assertIs(heartbeat['staging'], True)
        self.assertTrue(link.is_entitled())

    # --- what the answer does ---------------------------------------------------

    def test_a_staging_answer_keeps_the_database_a_copy(self):
        """Neutralized or not: a database the platform bills nothing for
        decrypts no mailbox credential, sends through no provider and says
        it is a copy on the settings page and in the Inbox."""
        secret = encryption_utils.encrypt_value(self.env, 'refresh-token')
        self.assertEqual(encryption_utils.decrypt_value(self.env, secret), 'refresh-token')
        with patch(POST, side_effect=self.staging_server()):
            link = self.License.action_connect()
            link.action_check_approval()
        self.assertTrue(database_is_copy(self.env))
        self.assertIs(encryption_utils.decrypt_value(self.env, secret), False)
        self.assertTrue(self.env['res.config.settings'].create({}).x_is_copy)
        self.assertTrue(self.env['mail.compose.message'].new({}).x_on_test_instance)
        # It says what it sees, not what it was told: an echo would keep it
        # staging for as long as it asked, after the platform moved it.
        self.calls.clear()
        with patch(POST, side_effect=self.staging_server()):
            link._heartbeat()
        self.assertNotIn('staging', self.sent(HEARTBEAT)[0])
        # A production answer gives production back.
        with patch(POST, side_effect=self.server()):
            link._heartbeat()
        self.assertFalse(database_is_copy(self.env))
        self.assertEqual(encryption_utils.decrypt_value(self.env, secret), 'refresh-token')

    def test_a_copy_still_signs_no_mailbox_in(self):
        """Connected or not, a sign-in is a credential for a live mailbox."""
        self.neutralize()
        with patch(POST, side_effect=self.staging_server()):
            link = self.License.action_connect()
            link.action_check_approval()
        self.calls.clear()
        with patch(POST, side_effect=self.staging_server()), \
                self.assertRaisesRegex(UserError, 'is a copy'):
            self.License.microsoft_start('https://odoo.test/mail_pro/microsoft/return')
        self.assertEqual(self.calls, [])

    def test_a_copy_does_not_record_how_the_inbox_is_used(self):
        self.neutralize()
        answer = self.signed(self.entitlement(
            staging=True, improve=True, improve_host='https://mailpro.test/i/7-abc',
            improve_token='phc_test', replay_sample=1))
        with patch(POST, side_effect=self.server(heartbeat=_response(200, answer))):
            link = self.License.action_connect()
            link.action_check_approval()
        self.assertTrue(link.improve)
        self.assertFalse(self.License.improve_active())

    # --- the Inbox's gate on a copy ---------------------------------------------

    def test_the_gate_on_a_copy_pairs_and_opens_once_approved(self):
        self.neutralize()
        self.assertEqual(self.License.inbox_gate(), {'connected': False, 'pairs': True})
        with patch(POST, side_effect=self.staging_server()):
            action = self.License.action_connect_open()
            self.assertEqual(action['url'], 'https://mailpro.test/link?code=ABCD-EFGH')
            gate = self.License.inbox_gate()
        self.assertEqual(gate, {'connected': True, 'pairs': False})
        self.assertIs(self.sent(START)[0]['staging'], True)

    def test_a_production_key_on_a_copy_with_another_uuid_connects_again(self):
        """The restored key was paired for production's uuid: the platform
        answers `wrong_database`, the gate offers Connect, and Connect pairs
        the copy as staging after forgetting the old key."""
        with patch(POST, side_effect=self.server()):
            link = self.License.action_connect()
            link.action_check_approval()
        self.neutralize()
        with patch(POST, side_effect=self.server(heartbeat=_response(
                403, {'status': 'wrong_database'}))):
            link._heartbeat()
        self.assertEqual(link.status, 'invalid')
        self.assertIn('Connect again', link.last_error)
        self.assertEqual(self.License.inbox_gate(), {'connected': False, 'pairs': True})
        self.calls.clear()
        with patch(POST, side_effect=self.staging_server()):
            self.License.action_connect()
        self.assertEqual(link.status, 'pending')
        self.assertFalse(link.key_encrypted)
        self.assertIs(self.sent(START)[0]['staging'], True)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestOnlyTheLinkReadsPastTheGate(TransactionCase):
    """`decrypt_link_secret` skips the copy gate. It is for the Pantalytics
    key and the pairing's device token, which open no mailbox; a mailbox
    credential read through it would undo the copy's whole promise."""

    def test_nothing_but_the_license_calls_it(self):
        models = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'models')
        callers = set()
        for folder, _dirs, files in os.walk(models):
            for name in files:
                if not name.endswith('.py'):
                    continue
                path = os.path.join(folder, name)
                with open(path, encoding='utf-8') as handle:
                    if re.search(r'\bdecrypt_link_secret\(', handle.read()):
                        callers.add(os.path.relpath(path, models))
        self.assertEqual(callers, {'encryption_utils.py', 'pan_mail_license.py'})
