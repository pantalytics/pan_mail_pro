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

from odoo import fields
from odoo.tests import tagged

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

    def test_notification_mailbox_never_goes_stale(self):
        """It carries the system email and reads nothing, so it cannot fall behind."""
        mailbox = self.notification_mailbox
        mailbox.last_check_date = (
            fields.Datetime.now() - timedelta(days=30))
        self.assertFalse(mailbox._sync_is_stale())

    # --------------------------------------------------------------- message

    def test_the_error_is_the_sentence(self):
        """One sentence, written once: the form's alert and the Inbox's mark."""
        self.mailbox.write({'state': 'error', 'error_message': 'Graph said 503'})
        self.mailbox.invalidate_recordset()
        self.assertEqual(self.mailbox.status_message, 'Graph said 503')
