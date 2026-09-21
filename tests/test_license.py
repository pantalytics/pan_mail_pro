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

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

from odoo.addons.pan_mail_pro.models import pan_mail_license
from odoo.addons.pan_mail_pro.models.pan_mail_license import canonical_json

POST = 'odoo.addons.pan_mail_pro.models.pan_mail_license.requests.post'

# What the server's Heartbeat model accepts. The module sending anything else
# would be refused there, and would be data that left the customer for nothing.
HEARTBEAT_FIELDS = {
    'db_uuid', 'module_version', 'odoo_version', 'mailboxes_connected',
    'mails_sent_24h', 'mails_received_24h', 'sync_ok', 'errors',
    'coverage', 'rules', 'corrections',
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
            'daily_send_limit': 25,
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
                    'verify_url_complete': 'https://mailpro.test/link?code=ABCD-EFGH',
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
        self.assertEqual(link.verify_url, 'https://mailpro.test/link?code=ABCD-EFGH')
        self.assertTrue(link.device_token_encrypted)
        self.assertNotIn('device-secret', link.device_token_encrypted)
        self.assertEqual(self.calls[0]['json']['db_uuid'], self.db_uuid)

    def test_the_settings_button_opens_the_page_with_the_code_in_it(self):
        with patch(POST, side_effect=self.server()):
            action = self.env['res.config.settings'].create({}).action_license_connect()
        self.assertEqual(action['type'], 'ir.actions.act_url')
        self.assertEqual(action['target'], 'new')
        self.assertEqual(action['url'], 'https://mailpro.test/link?code=ABCD-EFGH')

    def test_an_older_server_without_the_complete_link_still_works(self):
        def old_server(url, json=None, headers=None, timeout=None):
            return _response(200, {
                'status': 'started', 'user_code': 'ABCD-EFGH', 'device_token': 'x',
                'verify_url': 'https://mailpro.test/link', 'expires_at': '',
            })
        with patch(POST, side_effect=old_server):
            link = self.License.action_connect()
        self.assertEqual(link.verify_url, 'https://mailpro.test/link')

    def test_coming_back_from_the_approval_page_collects_the_key(self):
        with patch(POST, side_effect=self.server()):
            link = self.License.action_connect()
            link.collect_on_return()
        self.assertEqual(link.status, 'active')
        self.assertTrue(link.is_entitled())

    def test_coming_back_before_approving_changes_nothing(self):
        with patch(POST, side_effect=self.server(
                poll=_response(200, {'status': 'pending'}))):
            link = self.License.action_connect()
            link.collect_on_return()
        self.assertEqual(link.status, 'pending')

    def test_approval_stores_the_key_encrypted_and_reports_in(self):
        link = self.connected()
        self.assertEqual(link.status, 'active')
        self.assertFalse(link.user_code)
        self.assertNotIn('mpk_live_key', link.key_encrypted)
        heartbeat = self.calls[-1]
        self.assertTrue(heartbeat['url'].endswith('/api/v1/license/heartbeat'))
        self.assertEqual(heartbeat['headers']['Authorization'], 'Bearer mpk_live_key')
        self.assertTrue(link.is_entitled())
        self.assertEqual(link.daily_send_limit, 25)

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

    def test_a_bad_minute_on_our_side_keeps_the_pairing(self):
        """A 502 from the proxy during a deploy is not a verdict on the code.
        The device token stays, and the admin is told to try again."""
        with patch(POST, side_effect=self.server(poll=_response(502, {}))):
            link = self.License.action_connect()
            action = link.action_check_approval()
        self.assertEqual(link.status, 'pending')
        self.assertEqual(link.user_code, 'ABCD-EFGH')
        self.assertTrue(link.device_token_encrypted)
        self.assertEqual(action['params']['type'], 'warning')
        self.assertIn('502', action['params']['message'])

    def test_a_failed_first_heartbeat_keeps_the_key_and_is_retried(self):
        """The key arrives, the heartbeat behind it hits a 503: the instance
        is connected but not yet entitled, and says so rather than 'connect'.
        The fetch cron asks again after RETRY_MINUTES, not after a day."""
        link = self.connected(heartbeat=_response(503, {}))
        self.assertEqual(link.status, 'active')
        self.assertTrue(link.key_encrypted)
        self.assertFalse(link.is_entitled())
        self.assertTrue(link.last_error)
        self.assertIn('failed', self.License.not_allowed_error())
        self.assertNotIn('Connect this Odoo instance', self.License.not_allowed_error())

        with patch(POST, side_effect=self.server()):
            self.License._retry_if_stuck()  # too soon after the last check
            self.assertFalse(link.is_entitled())
            link.last_check = fields.Datetime.now() - timedelta(
                minutes=pan_mail_license.RETRY_MINUTES + 1)
            self.License._retry_if_stuck()
        self.assertTrue(link.is_entitled())
        self.assertFalse(link.last_error)

    def test_a_heartbeat_that_crashes_never_loses_the_key(self):
        """The server hands the key over exactly once. Whatever the heartbeat
        riding on that response does, the key survives it."""
        broken = _response(200, {'entitlement': 'not a dict', 'signature': 'x'})
        link = self.connected(heartbeat=broken)
        self.assertEqual(link.status, 'active')
        self.assertTrue(link.key_encrypted)
        self.assertTrue(link.last_error)

    def test_a_key_paired_for_another_database_is_refused(self):
        """A copy of the database carries the key and another uuid."""
        link = self.connected(heartbeat=_response(403, {'status': 'wrong_database'}))
        self.assertEqual(link.status, 'invalid')
        self.assertFalse(link.is_entitled())
        self.assertIn('another Odoo database', link.last_error)
        self.assertIn('no longer recognises', self.License.not_allowed_error())

    def test_the_refusal_names_the_state_it_is_in(self):
        License = self.License
        self.assertIn('Connect this Odoo instance', License.not_allowed_error())
        with patch(POST, side_effect=self.server()):
            link = License.action_connect()
        self.assertIn('waiting for approval', License.not_allowed_error())
        link.status = 'revoked'
        self.assertIn('replaced or revoked', License.not_allowed_error())
        link.status = 'canceled'
        self.assertIn('no Mail Pro subscription', License.not_allowed_error())

    def test_an_entitled_instance_is_not_reconnected_by_accident(self):
        """Connecting again would revoke the live key on the server. Disconnect
        is the deliberate step."""
        link = self.connected()
        with patch(POST, side_effect=self.server()), self.assertRaisesRegex(
                UserError, 'already connected'):
            self.License.action_connect()
        self.assertTrue(link.is_entitled())

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
        forged = self.signed(self.entitlement(daily_send_limit=999),
                             key=Ed25519PrivateKey.generate())
        link = self.connected(heartbeat=_response(200, forged))
        self.assertNotEqual(link.daily_send_limit, 999)
        self.assertFalse(link.is_entitled())
        self.assertTrue(link.last_error)

    def test_an_edited_answer_is_not_stored(self):
        body = self.signed(self.entitlement())
        body['entitlement']['daily_send_limit'] = 999
        link = self.connected(heartbeat=_response(200, body))
        self.assertNotEqual(link.daily_send_limit, 999)

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

    def test_the_heartbeat_carries_the_four_coverage_counts_and_the_rules(self):
        """Counts and rule names, the same four the Link Coverage screen shows."""
        self.env['mail.message'].sudo().create({
            'message_type': 'email', 'subject': 'Offerte', 'x_direction': 'incoming',
            'model': 'res.partner', 'res_id': self.env.user.partner_id.id,
        })
        self.connected()
        body = self.calls[-1]['json']
        self.assertEqual(set(body['coverage']), {'total', 'linked', 'contact_only', 'unlinked'})
        self.assertGreaterEqual(body['coverage']['contact_only'], 1)
        self.assertEqual(
            body['coverage']['total'],
            body['coverage']['linked'] + body['coverage']['contact_only']
            + body['coverage']['unlinked'])
        self.assertIsInstance(body['rules'], list)
        for row in body['rules']:
            self.assertEqual(set(row), {'rule', 'wins', 'corrected'})
        self.assertIsInstance(body['corrections'], int)
        self.assertNotIn('Offerte', json.dumps(body))

    def test_the_heartbeat_counts_what_this_module_sent_and_received(self):
        """Two numbers off `x_direction`, so a note and a system log are
        invisible to them and nothing but a count leaves."""
        Message = self.env['mail.message'].sudo()
        Message.create({
            'message_type': 'email', 'subject': 'Offerte 1',
            'x_direction': 'outgoing',
            'model': 'res.partner', 'res_id': self.env.user.partner_id.id,
        })
        Message.create({
            'message_type': 'email', 'subject': 'Re: Offerte 1',
            'x_direction': 'incoming',
            'model': 'res.partner', 'res_id': self.env.user.partner_id.id,
        })
        # A note this module never carried: counted by neither number.
        Message.create({
            'message_type': 'comment', 'subject': 'Bellen',
            'model': 'res.partner', 'res_id': self.env.user.partner_id.id,
        })
        self.connected()
        body = self.calls[-1]['json']
        self.assertEqual(body['mails_sent_24h'], 1)
        self.assertEqual(body['mails_received_24h'], 1)

    def test_a_mail_from_last_week_is_not_in_todays_counts(self):
        message = self.env['mail.message'].sudo().create({
            'message_type': 'email', 'x_direction': 'outgoing',
            'model': 'res.partner', 'res_id': self.env.user.partner_id.id,
        })
        message.date = datetime.now() - timedelta(days=7)
        self.connected()
        self.assertEqual(self.calls[-1]['json']['mails_sent_24h'], 0)

    # --- usage and billing are read at Pantalytics -----------------------------

    def test_the_settings_page_links_to_the_dashboard(self):
        """One link out, and no usage screen here: the number that decides the
        invoice is the one the server counted."""
        url = self.env['pan.mail.license'].dashboard_url()
        self.assertTrue(url.startswith('http'))
        self.assertTrue(url.endswith('/instances'))
        settings = self.env['res.config.settings'].create({})
        self.assertEqual(settings.x_license_dashboard_url, url)

    # --- help improve Mail Pro -------------------------------------------------

    def test_the_improve_switch_is_stored_off_the_signed_answer(self):
        link = self.connected(heartbeat=_response(200, self.signed(self.entitlement(
            improve=True, improve_host='https://mailpro.test/i/7-abc',
            improve_token='phc_test', replay_sample=0.5))))
        self.assertTrue(link.improve)
        self.assertEqual(link.improve_host, 'https://mailpro.test/i/7-abc')
        self.assertEqual(link.improve_token, 'phc_test')
        self.assertEqual(link.replay_sample, 0.5)
        self.assertTrue(self.License.improve_active())

    def test_an_answer_without_the_switch_means_off(self):
        """An older server, or a workspace that never said yes: nothing records."""
        link = self.connected()
        self.assertFalse(link.improve)
        self.assertFalse(link.improve_host)
        self.assertFalse(self.License.improve_active())

    def test_a_yes_without_a_host_is_a_no(self):
        self.connected(heartbeat=_response(200, self.signed(self.entitlement(
            improve=True, improve_host='', improve_token='phc_test', replay_sample=1))))
        self.assertFalse(self.License.improve_active())

    def test_an_unsigned_yes_is_not_stored(self):
        body = self.signed(self.entitlement())
        body['entitlement']['improve'] = True
        body['entitlement']['improve_host'] = 'https://evil.test/i/1-x'
        link = self.connected(heartbeat=_response(200, body))
        self.assertFalse(link.improve)
        self.assertFalse(self.License.improve_active())

    def test_the_odoo_administrator_can_refuse_but_never_enable(self):
        link = self.connected(heartbeat=_response(200, self.signed(self.entitlement(
            improve=True, improve_host='https://mailpro.test/i/7-abc',
            improve_token='phc_test', replay_sample=1))))
        self.env['ir.config_parameter'].sudo().set_param(
            pan_mail_license.IMPROVE_REFUSED_PARAM, True)
        self.assertFalse(self.License.improve_active())
        # The other way round: the refusal lifted, and the workspace now says no.
        self.env['ir.config_parameter'].sudo().set_param(
            pan_mail_license.IMPROVE_REFUSED_PARAM, False)
        with patch(POST, side_effect=self.server()):
            link._heartbeat()
        self.assertFalse(link.improve)
        self.assertFalse(self.License.improve_active())

    def test_a_neutralized_copy_never_records(self):
        self.connected(heartbeat=_response(200, self.signed(self.entitlement(
            improve=True, improve_host='https://mailpro.test/i/7-abc',
            improve_token='phc_test', replay_sample=1))))
        self.env['ir.config_parameter'].sudo().set_param('database.is_neutralized', True)
        self.assertFalse(self.License.improve_active())

    def test_disconnect_forgets_the_switch_too(self):
        link = self.connected(heartbeat=_response(200, self.signed(self.entitlement(
            improve=True, improve_host='https://mailpro.test/i/7-abc',
            improve_token='phc_test', replay_sample=1))))
        link.action_disconnect()
        self.assertFalse(link.improve)
        self.assertFalse(link.improve_host)
        self.assertFalse(link.improve_token)
        self.assertEqual(link.replay_sample, 0.0)

    def test_a_neutralized_copy_neither_connects_nor_reports(self):
        link = self.connected()
        self.calls.clear()
        self.env['ir.config_parameter'].sudo().set_param('database.is_neutralized', True)
        with patch(POST, side_effect=self.server()):
            link._heartbeat()
            with self.assertRaises(UserError):
                self.License.action_connect()
        self.assertEqual(self.calls, [])


@tagged('post_install', '-at_install')
class TestConnectedOnly(TransactionCase):
    """Not connected: incoming sync and new accounts stop, sending does not."""

    def setUp(self):
        super().setUp()
        # The real gate, not the yes every other test gets (tests/connected.py).
        self.env = self.env(context=dict(self.env.context, pan_mail_pro_real_gate=True))
        self.License = self.env['pan.mail.license']
        # A mailbox refuses to exist before the company's domains are known.
        if not self.env['pan.mail.domain'].sudo().search_count([]):
            self.env['pan.mail.domain'].sudo().create({'name': 'example.com'})

    def connect(self, valid_for=timedelta(days=14)):
        return self.License.sudo().create({
            'status': 'active', 'valid_until': fields.Datetime.now() + valid_for})

    def test_an_unconnected_instance_may_not_sync(self):
        self.assertFalse(self.License.sync_allowed())

    def test_a_connected_instance_may_sync(self):
        self.connect()
        self.assertTrue(self.License.sync_allowed())

    def test_a_connection_that_lapsed_offline_counts_as_none(self):
        self.connect(valid_for=timedelta(minutes=-1))
        self.assertFalse(self.License.sync_allowed())

    def test_a_revoked_connection_counts_as_none(self):
        link = self.connect()
        link.status = 'revoked'
        self.assertFalse(self.License.sync_allowed())

    def test_the_sync_cron_stops_and_says_why_on_the_mailboxes(self):
        mailbox = self.env['pan.mail.mailbox'].sudo().create({
            'email': 'gate@example.com', 'mailbox_type': 'shared'})
        mailbox.state = 'active'
        fetcher = self.env['pan.mail.fetcher']
        with patch.object(type(self.env['pan.mail.setup']), 'is_ready', return_value=True), \
                patch.object(type(self.env['pan.mail.mailbox']), '_has_working_credentials',
                             return_value=True), \
                patch.object(type(fetcher), '_process_mailbox') as process:
            fetcher._cron_fetch_incoming_mail()
        self.assertEqual(mailbox.state, 'error')
        self.assertIn('Connect this Odoo instance', mailbox.error_message)
        process.assert_not_called()

    def test_sync_now_says_why(self):
        mailbox = self.env['pan.mail.mailbox'].sudo().create({
            'email': 'gate-now@example.com', 'mailbox_type': 'shared'})
        with patch.object(type(self.env['pan.mail.setup']), 'is_ready', return_value=True), \
                self.assertRaisesRegex(UserError, 'Connect this Odoo instance'):
            mailbox.action_sync_now()

    def test_a_new_account_is_refused_and_an_existing_one_can_reconnect(self):
        link = self.connect()
        account = self.env['pan.mail.account'].sudo().create({
            'provider': 'imap', 'email': 'existing@example.com'})
        link.status = 'canceled'
        with self.assertRaises(UserError):
            self.env['pan.mail.account'].sudo().create({
                'provider': 'imap', 'email': 'new@example.com'})
        account.write({'email': 'existing@example.com'})

    def test_settings_say_whether_it_stopped(self):
        self.assertTrue(self.env['res.config.settings'].create({}).x_license_sync_blocked)
        self.connect()
        self.assertFalse(self.env['res.config.settings'].create({}).x_license_sync_blocked)


@tagged('post_install', '-at_install')
class TestLicenseReturnRoute(HttpCase):
    """The link on the approval page lands here, in the admin's own session."""

    def setUp(self):
        super().setUp()
        self.link = self.env['pan.mail.license'].sudo().create({'status': 'pending'})

    def test_an_administrator_lands_on_the_settings_page_and_the_key_is_collected(self):
        self.authenticate('admin', 'admin')
        with patch.object(type(self.link), 'collect_on_return', autospec=True) as collect:
            response = self.url_open('/mail_pro/pantalytics/return', allow_redirects=False)
        self.assertIn(response.status_code, (302, 303))
        self.assertIn('/odoo/settings#pan_mail_pro', response.headers['Location'])
        self.assertEqual(collect.call_count, 1)

    def test_somebody_who_is_not_an_administrator_collects_nothing(self):
        new_test_user(self.env, login='plain', password='plain-password-123', groups='base.group_user')
        self.authenticate('plain', 'plain-password-123')
        with patch.object(type(self.link), 'collect_on_return', autospec=True) as collect:
            self.url_open('/mail_pro/pantalytics/return', allow_redirects=False)
        self.assertEqual(collect.call_count, 0)
