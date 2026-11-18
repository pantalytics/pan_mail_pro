# -*- coding: utf-8 -*-
"""A quiet mailbox and a stopped mailbox are not the same thing.

Before `last_check_date` the model could not tell them apart. `last_sync_date`
is the fetch cursor -- the date of the newest message read -- so a mailbox
nobody has written to in a month reports a month-old "Last synced" while every
minute's run completes perfectly. The form said exactly that under exactly that
label, and the only way left to find out whether the sync worked was to press a
button.

These pin the three things the fix is worth nothing without: the heartbeat
moves on a run that read nothing, the cursor does not move with it, and going
quiet past the threshold is what turns the screen's one sentence on.
"""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged

from odoo.addons.pan_mail_pro.models.pan_mail_license import errors_signature
from odoo.addons.pan_mail_pro.models.pan_mail_mailbox import STALE_AFTER_MINUTES

from .common import MailProTestCase


@tagged('post_install', '-at_install', 'pan_mail_pro')
class TestMailboxHeartbeat(MailProTestCase):
    """The fixture's personal mailbox is the connected one (`sales@`)."""

    def setUp(self):
        super().setUp()
        self.mailbox = self.personal_mailbox
        # Stated rather than assumed: every assertion below about a *healthy*
        # mailbox is vacuous if the fixture's credentials do not work, and a
        # vacuous green test is the failure mode this module has shipped
        # before.
        self.assertTrue(
            self.mailbox._has_working_credentials(),
            'fixture must have a connected personal mailbox for this to mean anything')

    def _checked(self, minutes_ago):
        self.mailbox.last_check_date = (
            fields.Datetime.now() - timedelta(minutes=minutes_ago))

    def _working(self):
        """Nothing wrong with it beyond whatever the test is about to do."""
        self.mailbox.write({'state': 'active', 'error_message': False,
                            'sync_failure_count': 0})
        self.mailbox.invalidate_recordset()

    # ------------------------------------------------------------- heartbeat

    def test_success_moves_heartbeat_not_cursor(self):
        """The run finished. That is the fact recorded, and the only one."""
        cursor = fields.Datetime.now() - timedelta(days=30)
        self.mailbox.write({'last_sync_date': cursor, 'last_check_date': False})

        self.mailbox._record_sync_success()

        self.assertTrue(self.mailbox.last_check_date,
                        'a completed run left no heartbeat')
        self.assertEqual(
            self.mailbox.last_sync_date, cursor,
            'the fetch cursor moved on a run that read nothing')

    def test_heartbeat_moves_even_when_nothing_else_changed(self):
        """The common case: active, no error, no new mail, every minute.

        `_record_sync_success` used to write only when something had moved, so
        on the overwhelming majority of runs it wrote nothing at all -- which
        is precisely the case the screen needed evidence of.
        """
        self._working()
        before = fields.Datetime.now() - timedelta(hours=2)
        self.mailbox.last_check_date = before

        self.mailbox._record_sync_success()

        self.assertGreater(self.mailbox.last_check_date, before)

    # ----------------------------------------------------------------- stale

    def test_fresh_mailbox_is_healthy_and_says_nothing(self):
        self._working()
        self._checked(0)
        self.assertEqual(self.mailbox.health_status, 'healthy')
        self.assertFalse(self.mailbox.status_message,
                         'a working mailbox put a sentence on screen')

    def test_quiet_past_the_threshold_is_a_warning(self):
        """The failure nothing in the module could see: the cron stopped.

        Odoo deactivates a cron that keeps hitting its time limit. The mailbox
        stays `active`, its credentials still work, no error is ever recorded
        -- and mail simply stops arriving, on a form whose every indicator
        still reads OK.
        """
        self._working()
        self._checked(STALE_AFTER_MINUTES + 1)
        self.assertTrue(self.mailbox._sync_is_stale())
        self.assertEqual(self.mailbox.health_status, 'warning')
        self.assertIn(
            fields.Datetime.to_string(self.mailbox.last_check_date),
            self.mailbox.status_message or '',
            'the stale sentence does not say when it last worked')

    def test_just_inside_the_threshold_is_still_healthy(self):
        self._working()
        self._checked(STALE_AFTER_MINUTES - 1)
        self.assertFalse(self.mailbox._sync_is_stale())
        self.assertEqual(self.mailbox.health_status, 'healthy')

    def test_no_heartbeat_yet_is_not_stale(self):
        """An empty heartbeat is a mailbox that has not run, not one that stopped.

        Every mailbox in the database is in this state for the first minute
        after the upgrade, and a warning on all of them at once teaches the
        reader to ignore the mark on the one that means it.
        """
        self._working()
        self.mailbox.last_check_date = False
        self.assertFalse(self.mailbox._sync_is_stale())
        self.assertEqual(self.mailbox.health_status, 'healthy')

    def test_notification_mailbox_goes_stale_like_any_other(self):
        """It syncs whatever its level says (#387), so it can fall behind too.
        It used to be exempt, on the argument that it reads nothing."""
        mailbox = self.notification_mailbox
        mailbox.last_check_date = (
            fields.Datetime.now() - timedelta(days=30))
        self.assertTrue(mailbox._sync_is_stale())

    # --------------------------------------------------------------- message

    def test_the_error_is_the_sentence(self):
        """One sentence, written once: the form's alert and the Inbox's mark."""
        self.mailbox.write({'state': 'error', 'error_message': 'Graph said 503'})
        self.mailbox.invalidate_recordset()
        self.assertEqual(self.mailbox.status_message, 'Graph said 503')

    # ------------------------------------------------- reported to Pantalytics

    def _stale_in_heartbeat(self):
        body = self.env['pan.mail.license']._heartbeat_body()
        counts = {e['code']: e['count'] for e in body['errors']}
        return body, counts.get('incoming.sync_stale')

    def test_a_stale_mailbox_is_not_reported_healthy(self):
        """State `active` and a cron that stopped getting through: the form
        has said so since `last_check_date`, the heartbeat said `sync_ok`."""
        self._working()
        self._checked(STALE_AFTER_MINUTES + 5)

        body, stale = self._stale_in_heartbeat()

        self.assertFalse(body['sync_ok'])
        self.assertGreaterEqual(stale or 0, 1)

    def test_a_fresh_mailbox_adds_no_stale_count(self):
        self._working()
        self._checked(STALE_AFTER_MINUTES + 5)
        _body, before = self._stale_in_heartbeat()
        self._checked(1)
        _body, after = self._stale_in_heartbeat()
        self.assertEqual((after or 0), before - 1)

    # ------------------------------------------- reported once, not a minute

    def _pushes(self):
        """`_report_errors_if_new` as the fetch cron calls it, with a
        heartbeat that stores what it carried and never reaches a server.
        `last_check` stays empty, so the one-minute floor cannot hide a
        report that should not have been sent."""
        License = self.env['pan.mail.license']
        link = License.current() or License.sudo().create(
            {'status': 'active', 'key_encrypted': 'stored-key'})

        def heartbeat(record):
            record.errors_reported = errors_signature(
                License._heartbeat_body()['errors'])

        guarded = patch.object(type(License), '_heartbeat_guarded',
                               autospec=True, side_effect=heartbeat)
        return License, link, guarded

    def test_a_mailbox_that_stays_stale_is_not_reported_again(self):
        """The heartbeat carried `incoming.sync_stale` and the check compared
        the ledger only, so the two never agreed and the cron sent a
        heartbeat every minute for as long as the mailbox stayed stale."""
        self._working()
        self._checked(STALE_AFTER_MINUTES + 5)
        License, link, guarded = self._pushes()
        link.write({'errors_reported': errors_signature(
            License._heartbeat_body()['errors']), 'last_check': False})
        self.assertIn('incoming.sync_stale', link.errors_reported.split(','))
        with guarded as heartbeat:
            for _minute in range(3):
                License._report_errors_if_new()
            self.assertEqual(heartbeat.call_count, 0)

    def test_a_mailbox_that_goes_stale_is_reported_once(self):
        self._working()
        self._checked(STALE_AFTER_MINUTES + 5)
        License, link, guarded = self._pushes()
        link.write({'errors_reported': False, 'last_check': False})
        with guarded as heartbeat:
            for _minute in range(3):
                License._report_errors_if_new()
            self.assertEqual(heartbeat.call_count, 1)
            self.assertIn('incoming.sync_stale', link.errors_reported.split(','))

    def test_a_stale_mailbox_that_recovers_is_reported_once(self):
        self._working()
        self._checked(STALE_AFTER_MINUTES + 5)
        License, link, guarded = self._pushes()
        link.write({'errors_reported': False, 'last_check': False})
        with guarded as heartbeat:
            License._report_errors_if_new()
            self._checked(1)
            for _minute in range(3):
                License._report_errors_if_new()
            self.assertEqual(heartbeat.call_count, 2,
                             'the stale code going away is news once')
            self.assertNotIn('incoming.sync_stale', (link.errors_reported or '').split(','))

    def test_a_new_code_beside_a_stale_mailbox_is_still_reported(self):
        self._working()
        self._checked(STALE_AFTER_MINUTES + 5)
        License, link, guarded = self._pushes()
        link.write({'errors_reported': False, 'last_check': False})
        with guarded as heartbeat:
            License._report_errors_if_new()
            self.env['pan.mail.error']._record('outgoing.send_failed', detail='no')
            License._report_errors_if_new()
            License._report_errors_if_new()
            self.assertEqual(heartbeat.call_count, 2)

    def test_the_code_limit_is_compared_as_it_is_sent(self):
        """At the cap the stale code pushes the last ledger code out of the
        heartbeat; the check must leave it out too, or it never agrees."""
        self._working()
        self._checked(STALE_AFTER_MINUTES + 5)
        Error = self.env['pan.mail.error']
        Error._record('outgoing.send_failed', detail='no')
        Error._record('outgoing.send_failed', detail='no again')
        Error._record('oauth.token_revoked', detail='invalid_grant')
        License, link, guarded = self._pushes()
        link.write({'errors_reported': False, 'last_check': False})
        cap = 'odoo.addons.pan_mail_pro.models.pan_mail_license.MAX_CODES_REPORTED'
        with guarded as heartbeat, patch(cap, 2):
            for _minute in range(3):
                License._report_errors_if_new()
            self.assertEqual(heartbeat.call_count, 1)
            reported = link.errors_reported.split(',')
            self.assertEqual(len(reported), 2)
            self.assertIn('incoming.sync_stale', reported)
