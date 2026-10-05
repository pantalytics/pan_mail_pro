# -*- coding: utf-8 -*-
"""
The error ledger: what a caught failure leaves behind, and what of it travels.

A failure used to be a line in a log file nobody opens. These tests pin the
four things that make `pan.mail.error` worth having: a caught failure leaves a
row with a code from the fixed list, every code a call site uses *is* on that
list, the heartbeat carries codes and counts and nothing that names anyone, and
a kind of failure the last day had not seen is reported within the minute.
"""
import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.pan_mail_pro.models import ir_http as ir_http_module
from odoo.addons.pan_mail_pro.models.pan_mail_error import (
    CODES, FLOWS, MAX_CODES_REPORTED, RETENTION_DAYS,
)
from odoo.addons.pan_mail_pro.models.pan_mail_license import PanMailLicense

MODULE = Path(__file__).resolve().parent.parent
GUARDED = 'odoo.addons.pan_mail_pro.models.pan_mail_license.PanMailLicense._heartbeat_guarded'


def _module_sources():
    for folder in ('models', 'controllers'):
        yield from (MODULE / folder).rglob('*.py')


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestErrorLedger(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['pan.mail.domain'].set_domains(['gate-fixture.test'])
        cls.Error = cls.env['pan.mail.error']
        cls.mailbox = cls.env['pan.mail.mailbox'].create({'email': 'support@company.test'})

    def _boom(self):
        raise ValueError('Mailbox boss@acme.example refused "Offerte 2024"')

    def _caught(self):
        try:
            self._boom()
        except ValueError as error:
            return error

    # --- the vocabulary ------------------------------------------------------

    def test_every_code_names_a_flow(self):
        flows = dict(FLOWS)
        for code in CODES:
            self.assertIn(code.split('.', 1)[0], flows, code)
            self.assertLessEqual(len(code), 40, code)

    def test_every_code_a_call_site_uses_is_in_the_list(self):
        """A code is a fixed string the server and PostHog group on. One typed
        at a call site and nowhere else is a chart with one row nobody
        recognises."""
        pattern = re.compile(r"""\.record\(\s*['"]([a-z_]+\.[a-z_]+)['"]""")
        used = set()
        for source in _module_sources():
            used.update(pattern.findall(source.read_text()))
        self.assertTrue(used, 'no call site records an error')
        self.assertLessEqual(used, set(CODES), used - set(CODES))

    # --- a row ---------------------------------------------------------------

    def test_a_caught_exception_leaves_a_row_with_its_traceback(self):
        self.Error.record('incoming.mailbox_failed', self._caught(), mailbox=self.mailbox)
        row = self.Error.search([('code', '=', 'incoming.mailbox_failed')], limit=1)
        self.assertTrue(row)
        self.assertEqual(row.flow, 'incoming')
        self.assertEqual(row.level, 'error')
        self.assertEqual(row.mailbox_id, self.mailbox)
        self.assertEqual(row.provider, self.mailbox.provider)
        self.assertTrue(row.message.startswith('ValueError: Mailbox'))
        self.assertIn('_boom', row.traceback)
        self.assertEqual(row.description, CODES['incoming.mailbox_failed'])

    def test_a_detail_without_an_exception_is_a_row_too(self):
        self.Error.record('outgoing.throttled', level='warning', detail='Retry after 60s')
        row = self.Error.search([('code', '=', 'outgoing.throttled')], limit=1)
        self.assertEqual(row.level, 'warning')
        self.assertEqual(row.message, 'Retry after 60s')
        self.assertFalse(row.traceback)

    def test_recording_never_raises(self):
        """A failure to record a failure is a log line, not a second failure."""
        with patch.object(type(self.Error), '_values', side_effect=RuntimeError('db gone')):
            self.Error.record('incoming.mailbox_failed', self._caught())

    def test_a_failed_send_is_recorded_under_its_code(self):
        mail = self.env['mail.mail'].create({
            'subject': 'Hello', 'email_to': 'someone@example.com', 'body_html': '<p>x</p>',
        })
        mail._fail('The provider said no')
        row = self.Error.search([('code', '=', 'outgoing.send_failed')], limit=1)
        self.assertTrue(row)
        self.assertEqual(row.message, 'The provider said no')

    def test_a_request_that_failed_inside_the_module_is_recorded(self):
        request = MagicMock()
        request.env = self.env
        with patch.object(ir_http_module, 'request', request):
            self.env['ir.http']._pan_mail_record_failure(self._caught())
        self.assertTrue(self.Error.search([('code', '=', 'inbox.rpc_failed')]))

    def test_an_expected_refusal_is_not_an_error(self):
        request = MagicMock()
        request.env = self.env
        try:
            raise UserError('Connect to Pantalytics first')
        except UserError as refusal:
            with patch.object(ir_http_module, 'request', request):
                self.env['ir.http']._pan_mail_record_failure(refusal)
        self.assertFalse(self.Error.search([('code', '=', 'inbox.rpc_failed')]))

    def test_old_rows_are_vacuumed(self):
        self.Error.record('license.heartbeat_failed', detail='old')
        row = self.Error.search([('code', '=', 'license.heartbeat_failed')], limit=1)
        self.env.cr.execute(
            'UPDATE pan_mail_error SET create_date = %s WHERE id = %s',
            (fields.Datetime.subtract(fields.Datetime.now(), days=RETENTION_DAYS + 1), row.id))
        self.Error.invalidate_model()
        self.Error._gc_errors()
        self.assertFalse(row.exists())

    # --- what the heartbeat carries ------------------------------------------

    def test_codes_since_counts_per_code_most_frequent_first(self):
        since = fields.Datetime.now()
        for _ in range(3):
            self.Error.record('incoming.message_failed', detail='x', mailbox=self.mailbox)
        self.Error.record('outgoing.no_route', detail='y')
        codes = self.Error.codes_since(since)
        self.assertEqual(codes[:2], [
            {'code': 'incoming.message_failed', 'count': 3},
            {'code': 'outgoing.no_route', 'count': 1},
        ])
        self.assertLessEqual(len(codes), MAX_CODES_REPORTED)
        self.assertEqual(
            self.Error.signature_since(since), 'incoming.message_failed,outgoing.no_route')

    def test_the_heartbeat_carries_codes_and_counts_and_no_address(self):
        self.Error.record('incoming.mailbox_failed', self._caught(), mailbox=self.mailbox)
        body = self.env['pan.mail.license']._heartbeat_body()
        self.assertIn({'code': 'incoming.mailbox_failed', 'count': 1}, body['errors'])
        for entry in body['errors']:
            self.assertEqual(set(entry), {'code', 'count'})
            self.assertIn(entry['code'], CODES)
        self.assertNotIn('@', json.dumps(body['errors']))
        self.assertNotIn('Offerte', json.dumps(body))

    def test_a_new_kind_of_failure_is_reported_within_the_minute(self):
        """Same contract as the setup push: once per change, never once per
        failure, and not again once the heartbeat has carried the set."""
        License = self.env['pan.mail.license']
        link = License.sudo().create({'status': 'active', 'key_encrypted': 'stored-key'})
        self.assertEqual(License.current(), link)
        with patch(GUARDED, autospec=True) as guarded:
            License._report_errors_if_new()
            self.assertEqual(guarded.call_count, 0, 'nothing failed, nothing to report')
            self.Error.record('outgoing.send_failed', detail='no')
            self.Error.record('outgoing.send_failed', detail='no again')
            License._report_errors_if_new()
            self.assertEqual(guarded.call_count, 1)
            # The heartbeat stores what it carried; the same set is not news.
            link.errors_reported = 'outgoing.send_failed'
            link.last_check = False
            License._report_errors_if_new()
            self.assertEqual(guarded.call_count, 1)
            self.Error.record('oauth.token_revoked', detail='invalid_grant')
            License._report_errors_if_new()
            self.assertEqual(guarded.call_count, 2)

    def test_the_heartbeat_stores_the_codes_it_carried(self):
        self.assertTrue(hasattr(PanMailLicense, '_report_errors_if_new'))
        source = (MODULE / 'models' / 'pan_mail_license.py').read_text()
        self.assertIn("self.errors_reported = ','.join(sorted(e['code'] for e in report['errors']))",
                      source)

    # --- the browser side ----------------------------------------------------

    def test_the_inbox_reports_its_errors_with_the_message_scrubbed(self):
        """No JS runner in the suite; the shape is asserted on the source and
        the behaviour in the browser by tools/ui_check.py."""
        improve = (MODULE / 'static' / 'src' / 'js' / 'improve.js').read_text()
        self.assertIn('before_send: scrubExceptionEvent', improve)
        self.assertIn('capture_exceptions: false', improve,
                      'the SDK must not fetch its autocapture extension')
        self.assertIn('posthog.captureException(', improve)
        self.assertIn('addEventListener("unhandledrejection"', improve)
        view = (MODULE / 'static' / 'src' / 'js' / 'conversation_view' / 'conversation_view.js'
                ).read_text()
        self.assertGreaterEqual(view.count('this.improve.failed('), 7)
