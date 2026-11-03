# -*- coding: utf-8 -*-
"""The month's mails: what the heartbeat counts, and what the answer may pause.

The plan is odoo-mcp-pro-admin `docs/plans/mail-pro-billing.md` (pan_mail_pro#363):
500 mails a month per Odoo instance are included, sent and received both
count, a mailbox's first import does not. Over that with no card on the
workspace, incoming waits and sending goes on.

Two halves. The count is the meter the platform bills from, so it is driven
through the real import (`_process_message`) on every outcome the routing log
knows, because #356 was a count that saw only one of them. The pause is driven
through a real signed answer, like test_license.py, because what pauses
somebody's mail must be something our server said.
"""
import base64
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.pan_mail_pro.models import encryption_utils, pan_mail_license
from odoo.addons.pan_mail_pro.models.pan_mail_license import RETRY_MINUTES, canonical_json

from ..models.mail_provider_client import FOLDER_INBOX
from .common import MailProTestCase

POST = 'odoo.addons.pan_mail_pro.models.pan_mail_license.requests.post'


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestMailCount(MailProTestCase):
    """`mails_received_24h` and `mails_sent_24h`, as the heartbeat sends them."""

    def setUp(self):
        super().setUp()
        self.processor = self.env['pan.mail.fetcher']
        self.mailbox = self.personal_mailbox
        self.mailbox.write({'sync_level': 'everyone'})
        self.since = fields.Datetime.now() - timedelta(hours=24)
        self.License = self.env['pan.mail.license']

    def _import(self, message_id, sender, subject, date=None, headers=None):
        message = {
            'provider_message_id': message_id.strip('<>'),
            'message_id': message_id,
            'subject': subject,
            'from': {'email': sender, 'name': sender.split('@')[0]},
            'to': [{'email': self.mailbox.email, 'name': 'Sales'}],
            'cc': [],
            'date': fields.Datetime.to_string(date or fields.Datetime.now()),
            'body_html': '<p>Hello</p>',
            'body_is_html': True,
            'has_attachments': False,
            'headers': dict({'message-id': message_id}, **(headers or {})),
        }
        client = type(self.env['microsoft.graph.client'])
        with patch.object(client, 'get_message', autospec=True, return_value=message), \
                patch.object(client, 'get_message_attachments', autospec=True,
                             return_value=[]):
            self.processor._process_message(self.mailbox, message, FOLDER_INBOX)
        return self.env['pan.mail.routing.log'].search(
            [('internet_message_id', '=', message_id)], limit=1)

    def received(self):
        return self.License._mail_counts_since(self.since)['incoming']

    def test_every_imported_mail_counts_however_it_landed(self):
        """#356: the platform saw 53 received mails where the routing log
        held 81, and 53 was about the threaded ones. Every outcome of the
        sync is a mail that reached Odoo, so every one counts."""
        before = self.received()
        first = self._import('<limit-a@customer.test>', 'anna@customer.test', 'Question')
        reply = self._import('<limit-b@customer.test>', 'anna@customer.test', 'Re: Question',
                             headers={'in-reply-to': '<limit-a@customer.test>',
                                      'references': '<limit-a@customer.test>'})
        alias = self.env['mail.alias'].create({
            'alias_name': 'leads-limit-fixture',
            'alias_model_id': self.env['ir.model']._get_id('crm.lead'),
        })
        self.mailbox.write({'route_to_team': True, 'alias_id': alias.id})
        created = self._import('<limit-c@other.test>', 'bram@other.test', 'New request')
        self.assertEqual((first.outcome, reply.outcome, created.outcome),
                         ('fallback', 'threaded', 'created'))
        self.assertEqual(self.received() - before, 3)

    def test_a_first_import_does_not_count(self):
        """A mail dated before its mailbox existed came in because the start
        date reached back into history: the customer's past, not this
        month's mail."""
        before = self.received()
        history = self.mailbox.create_date - timedelta(days=30)
        log = self._import('<limit-old@customer.test>', 'anna@customer.test',
                           'Last month', date=history)
        self.assertTrue(log.mail_message_id, 'the old mail is imported all the same')
        self.assertEqual(self.received(), before)

    def test_a_mail_that_arrives_late_counts_on_the_day_it_lands(self):
        """A mailbox that was offline catches up: the mail is dated two days
        ago, after the mailbox existed, and lands today. Today's heartbeat is
        the only one that can still carry it."""
        before = self.received()
        self.mailbox.flush_recordset()
        self.env.cr.execute('UPDATE pan_mail_mailbox SET create_date = %s WHERE id = %s',
                            (fields.Datetime.now() - timedelta(days=10), self.mailbox.id))
        self.mailbox.invalidate_recordset()
        self._import('<limit-late@customer.test>', 'anna@customer.test', 'Sent Monday',
                     date=fields.Datetime.now() - timedelta(days=2))
        self.assertEqual(self.received() - before, 1)

    def test_sent_mail_follows_the_same_rules(self):
        """Odoo's own sent mail has a mailbox and today's date, so it counts;
        a sent item from before the mailbox existed is history like any other."""
        Message = self.env['mail.message'].sudo()
        before = self.License._mail_counts_since(self.since)['outgoing']
        values = {'message_type': 'email', 'x_direction': 'outgoing',
                  'x_mailbox_id': self.mailbox.id,
                  'model': 'res.partner', 'res_id': self.env.user.partner_id.id}
        Message.create(values)
        Message.create(dict(values, date=self.mailbox.create_date - timedelta(days=90)))
        self.assertEqual(self.License._mail_counts_since(self.since)['outgoing'] - before, 1)

    # -- the days the heartbeat sends (#599) --------------------------------

    def _sent_at(self, when):
        """One outgoing mail on this mailbox, stamped as reaching Odoo at
        `when` (naive UTC, as Odoo stores it)."""
        message = self.env['mail.message'].sudo().create({
            'message_type': 'email', 'x_direction': 'outgoing',
            'x_mailbox_id': self.mailbox.id,
            'model': 'res.partner', 'res_id': self.env.user.partner_id.id,
            'date': when})
        message.flush_recordset()
        self.env.cr.execute('UPDATE mail_message SET create_date = %s WHERE id = %s',
                            (when, message.id))
        message.invalidate_recordset()
        self.env.cr.execute('UPDATE pan_mail_mailbox SET create_date = %s WHERE id = %s',
                            (when - timedelta(days=30), self.mailbox.id))
        self.mailbox.invalidate_recordset()
        return message

    def _days(self, now, user=None):
        License = self.License.with_user(user) if user else self.License
        return {d['day']: d['sent'] for d in License.sudo()._mails_by_day(now)}

    def test_the_days_are_utc_calendar_days_around_midnight(self):
        """23:59:59 and 00:00:00 UTC are two days, whatever the clock of the
        heartbeat that reports them."""
        midnight = datetime(2026, 11, 3)
        before = self._days(midnight + timedelta(hours=8))
        self._sent_at(midnight - timedelta(seconds=1))
        self._sent_at(midnight)
        after = self._days(midnight + timedelta(hours=8))
        self.assertEqual(after['2026-11-02'] - before['2026-11-02'], 1)
        self.assertEqual(after['2026-11-03'] - before['2026-11-03'], 1)

    def test_the_admins_timezone_moves_no_mail(self):
        midnight = datetime(2026, 11, 3)
        self._sent_at(midnight + timedelta(minutes=30))
        self.env.user.tz = 'UTC'
        utc = self._days(midnight + timedelta(hours=8), self.env.user)
        self.env.user.tz = 'Pacific/Auckland'
        self.assertEqual(self._days(midnight + timedelta(hours=8), self.env.user), utc)

    def test_a_week_newest_first_and_the_same_every_time(self):
        now = datetime(2026, 11, 3, 20, 15)
        self._sent_at(now - timedelta(days=6, hours=20))
        days = self.License._mails_by_day(now)
        self.assertEqual([d['day'] for d in days],
                         ['2026-11-0%d' % n for n in range(3, 0, -1)]
                         + ['2026-10-31', '2026-10-30', '2026-10-29', '2026-10-28'])
        self.assertEqual(len(days), pan_mail_license.MAIL_DAYS)
        self.assertTrue(all(self.License._mails_by_day(now) == days for _ in range(10)))

    def test_a_first_import_is_left_out_of_its_day(self):
        now = datetime(2026, 11, 3, 12)
        before = self._days(now)
        message = self._sent_at(now - timedelta(hours=2))
        message.write({'date': self.mailbox.create_date - timedelta(days=1)})
        self.assertEqual(self._days(now), before)

    def test_the_heartbeat_carries_the_week(self):
        body = self.License._heartbeat_body()
        self.assertEqual(len(body['mails_by_day']), pan_mail_license.MAIL_DAYS)
        self.assertEqual(body['mails_by_day'][0]['day'],
                         fields.Datetime.now().date().isoformat())


def _response(status_code, body):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = body
    return response


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestMailLimit(TransactionCase):
    """`mails_included`, `mails_used_month` and `card` on the signed answer."""

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
        key_patch = patch.object(pan_mail_license, 'PUBLIC_KEY', self.public_key)
        key_patch.start()
        self.addCleanup(key_patch.stop)
        # The real gate, not tests/connected.py's yes: what pauses is asked
        # after it, so it has to be standing.
        self.env = self.env(context=dict(self.env.context, pan_mail_pro_real_gate=True))
        self.License = self.env['pan.mail.license']
        self.link = self.License.sudo().create({
            'status': 'active',
            'key_encrypted': encryption_utils.encrypt_value(self.env, 'mpk_live_key'),
        })
        if not self.env['pan.mail.domain'].sudo().search_count([]):
            self.env['pan.mail.domain'].sudo().create({'name': 'example.com'})
        self.calls = 0

    def server(self, **fields_):
        """Our server at `requests.post`, answering every heartbeat with a
        signed entitlement that carries `fields_`."""
        payload = {
            'version': 1, 'installation_id': 7, 'db_uuid': self.db_uuid,
            'status': 'active', 'plan': 'standard', 'daily_send_limit': 200,
            'valid_until': (datetime.now(timezone.utc) + timedelta(days=14)).replace(
                microsecond=0).isoformat(),
            'latest_version': '', 'message': '',
        }
        payload.update(fields_)
        signature = base64.b64encode(self.signing_key.sign(canonical_json(payload))).decode()

        def handler(url, json=None, headers=None, timeout=None):
            self.calls += 1
            return _response(200, {'entitlement': payload, 'signature': signature})
        return patch(POST, side_effect=handler)

    def answer(self, **fields_):
        """One heartbeat, and the answer stored."""
        with self.server(**fields_):
            self.link._heartbeat()

    def over(self, **overrides):
        return dict({'mails_included': 500, 'mails_used_month': 512, 'card': False}, **overrides)

    # --- what the answer says -------------------------------------------

    def test_an_answer_from_an_older_platform_means_no_limit(self):
        self.answer()
        self.assertTrue(self.License.sync_allowed())
        self.assertEqual(self.link.mail_usage(),
                         {'mails_included': 0, 'mails_used_month': 0, 'card': False})
        self.assertFalse(self.License.incoming_paused())

    def test_over_the_included_mails_without_a_card_pauses_incoming(self):
        self.answer(**self.over())
        self.assertEqual(self.link.mail_usage(),
                         {'mails_included': 500, 'mails_used_month': 512, 'card': False})
        self.assertTrue(self.License.incoming_paused())
        # Connected all the same: the Inbox, new mailboxes and sending do not
        # ask this question.
        self.assertTrue(self.License.sync_allowed())

    def test_room_left_a_card_or_no_limit_keeps_it_running(self):
        for case in (self.over(mails_used_month=499), self.over(card=True),
                     self.over(mails_included=0), self.over(mails_included='lots'),
                     self.over(mails_included=True), self.over(card='yes')):
            with self.subTest(case=case):
                self.answer(**case)
                if case.get('card') == 'yes':
                    # Only a real true is a card: a string is not one.
                    self.assertTrue(self.License.incoming_paused())
                else:
                    self.assertFalse(self.License.incoming_paused())

    def test_exactly_the_included_mails_is_the_limit(self):
        self.answer(**self.over(mails_used_month=500))
        self.assertTrue(self.License.incoming_paused())

    def test_an_answer_that_is_not_ours_changes_nothing(self):
        self.answer(**self.over())
        forged = dict(self.over(card=True), db_uuid=self.db_uuid)

        def handler(url, json=None, headers=None, timeout=None):
            return _response(200, {'entitlement': forged, 'signature': 'bm9wZQ=='})
        with patch(POST, side_effect=handler):
            self.link._heartbeat()
        self.assertTrue(self.License.incoming_paused())

    # --- what it pauses ----------------------------------------------------

    def test_the_sync_cron_pauses_with_its_own_reason(self):
        self.answer(**self.over())
        mailbox = self.env['pan.mail.mailbox'].sudo().create({
            'email': 'limit@example.com', 'mailbox_type': 'shared'})
        mailbox.state = 'active'
        fetcher = self.env['pan.mail.fetcher']
        with patch.object(type(self.env['pan.mail.setup']), 'is_ready', return_value=True), \
                patch.object(type(self.env['pan.mail.mailbox']), '_has_working_credentials',
                             return_value=True), \
                patch.object(type(fetcher), '_process_mailbox') as process:
            fetcher._cron_fetch_incoming_mail()
        self.assertEqual(mailbox.state, 'error')
        self.assertIn('500 mails this month. Add a card to keep receiving.',
                      mailbox.error_message)
        self.assertIn('Sending keeps working', mailbox.error_message)
        process.assert_not_called()

    def test_sync_now_says_why(self):
        self.answer(**self.over())
        mailbox = self.env['pan.mail.mailbox'].sudo().create({
            'email': 'limit-now@example.com', 'mailbox_type': 'shared'})
        with patch.object(type(self.env['pan.mail.setup']), 'is_ready', return_value=True), \
                self.assertRaisesRegex(UserError, '500 mails this month'):
            mailbox.action_sync_now()

    def test_paused_asks_again_and_a_card_lifts_it(self):
        """The platform lifts it (a card, the 1st of the month); only an
        answer brings that here, so the fetch cron asks every RETRY_MINUTES
        while it lasts, and not at all once it is gone."""
        self.answer(**self.over())
        calls = self.calls
        self.License._retry_if_stuck()
        self.assertEqual(self.calls, calls, 'just asked: not again yet')

        self.link.last_check = fields.Datetime.now() - timedelta(minutes=RETRY_MINUTES + 1)
        with self.server(**self.over(card=True)):
            self.License._retry_if_stuck()
        self.assertEqual(self.calls, calls + 1)
        self.assertFalse(self.License.incoming_paused())

        self.link.last_check = fields.Datetime.now() - timedelta(minutes=RETRY_MINUTES + 1)
        with self.server():
            self.License._retry_if_stuck()
        self.assertEqual(self.calls, calls + 1, 'running again: no extra heartbeat')

    # --- what Settings says ------------------------------------------------

    def test_settings_step_one_says_what_to_do_and_steps_two_to_four_stay(self):
        settings = self.env['res.config.settings'].create({})
        self.assertFalse(settings.x_license_mail_paused)
        self.assertFalse(settings.x_license_mail_paused_msg)

        self.answer(**self.over())
        settings = self.env['res.config.settings'].create({})
        self.assertTrue(settings.x_license_mail_paused)
        self.assertEqual(settings.x_license_mail_paused_msg,
                         '500 mails this month. Add a card to keep receiving.')
        # Connected and done: what hangs on step 1 stays on the page.
        self.assertEqual(settings.x_license_state, 'connected')
        self.assertTrue(settings.x_setup_account_done)
        self.assertFalse(settings.x_license_sync_blocked)
        # Step 1 only reports it (#382): Add a card and Check again are on
        # the account page its arrow opens.
        self.assertTrue(self.link.mail_paused)
        self.assertTrue(self.link.check_again)
        action = self.link.action_open_dashboard()
        self.assertEqual(action['url'], self.License.dashboard_url())
        self.assertEqual(action['target'], 'new')

    def test_check_again_asks_now(self):
        """The link beside Add a card: back from the Pantalytics tab, one
        click asks now instead of waiting for the cron."""
        self.answer(**self.over())
        with self.server(**self.over(card=True)):
            self.link.action_check_approval()
        self.assertFalse(self.License.incoming_paused())
