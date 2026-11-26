# -*- coding: utf-8 -*-
"""The mailbox page and its Sync page (the Mail Pro canvas, 04 to 09).

A run per pass, its counts, the stop after SYNC_FAILURE_LIMIT failed runs and
the way back, the one status word, and the Sync page as a second form of the
same record.
"""
from datetime import timedelta
from unittest.mock import patch

from lxml import etree

from odoo import fields
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.pan_mail_pro.models.pan_mail_mailbox import SYNC_FAILURE_LIMIT
from odoo.addons.pan_mail_pro.models.pan_mail_sync_run import RUN_CTX


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestSyncRun(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['pan.mail.domain'].set_domains(['gate-fixture.test'])
        cls.processor = cls.env['pan.mail.fetcher']
        cls.Run = cls.env['pan.mail.sync.run']
        cls.mailbox = cls.env['pan.mail.mailbox'].create({
            'email': 'runs@company.test',
            'state': 'active',
        })

    def _cron(self, process):
        Mailbox = type(self.mailbox)
        with patch.object(type(self.processor), '_process_mailbox', process), \
             patch.object(Mailbox, '_has_working_credentials',
                          return_value=True, autospec=True), \
             patch.object(Mailbox, '_access_check_due',
                          return_value=False, autospec=True), \
             patch.object(type(self.env['pan.mail.setup']), 'is_ready',
                          return_value=True, autospec=True), \
             patch.object(type(self.env['pan.mail.license']), 'sync_allowed',
                          return_value=True, autospec=True):
            self.processor._cron_fetch_incoming_mail()

    def _runs(self):
        return self.Run.search([('mailbox_id', '=', self.mailbox.id)])

    def test_a_pass_writes_one_run_with_its_counts(self):
        def process(self_, mailbox):
            run = self_._current_run()
            self.assertEqual(run.id, self_.env.context.get(RUN_CTX))
            run._note_read(4)
            run._note_error()
            self_.env['pan.mail.routing.log'].log_decision(
                mailbox=mailbox, match={}, outcome='fallback',
                subject='Hello', email_from='a@b.test')
            return None

        self._cron(process)
        run = self._runs()
        self.assertEqual(len(run), 1)
        self.assertEqual(run.kind, 'incoming')
        self.assertEqual(run.state, 'done')
        self.assertEqual((run.read_count, run.linked_count, run.error_count,
                          run.skipped_count), (4, 1, 1, 2))
        self.assertEqual(run.log_ids.subject, 'Hello')
        self.assertFalse(run.started_by, 'the cron starts its own runs')

    def test_a_pass_that_raises_still_leaves_its_run_failed(self):
        def process(self_, mailbox):
            raise ValueError('503 Service Unavailable')

        with mute_logger('odoo.addons.pan_mail_pro.models.pan_mail_fetcher'):
            self._cron(process)
        run = self._runs()
        self.assertEqual(run.state, 'failed')
        self.assertIn('503', run.message)

    def test_the_limit_stops_the_sync_and_says_so_once(self):
        def process(self_, mailbox):
            raise ValueError('503 Service Unavailable')

        with mute_logger('odoo.addons.pan_mail_pro.models.pan_mail_fetcher'):
            for _ in range(SYNC_FAILURE_LIMIT):
                self._cron(process)
        self.assertFalse(self.mailbox.sync_enabled)
        self.assertEqual(self.mailbox.sync_status, 'stopped')
        stops = self.mailbox.message_ids.filtered(lambda m: 'Sync stopped' in (m.body or ''))
        self.assertEqual(len(stops), 1)

        # Stopped is not read any more.
        with mute_logger('odoo.addons.pan_mail_pro.models.pan_mail_fetcher'):
            self._cron(process)
        self.assertEqual(len(self._runs()), SYNC_FAILURE_LIMIT)

        # A run that goes through (Run Now) resumes it.
        self.mailbox._record_sync_success()
        self.assertTrue(self.mailbox.sync_enabled)
        self.assertEqual(self.mailbox.sync_status, 'syncing')

    def test_turning_it_on_starts_the_count_afresh(self):
        self.mailbox.write({'sync_enabled': False,
                            'sync_failure_count': SYNC_FAILURE_LIMIT})
        self.assertEqual(self.mailbox.sync_status, 'stopped')
        self.mailbox.sync_enabled = True
        self.assertEqual(self.mailbox.sync_failure_count, 0)

    def test_pause_and_resume_are_a_persons(self):
        """Pause is Paused, never Stopped, and both say so once in the
        chatter; Resume starts the count afresh."""
        self.mailbox.last_check_date = fields.Datetime.now()
        self.mailbox.sync_failure_count = 2
        self.mailbox.action_pause()
        self.assertFalse(self.mailbox.sync_enabled)
        self.assertEqual(self.mailbox.sync_status, 'paused')
        self.assertEqual(self.mailbox.sync_failure_count, 2, 'a pause hides no failed run')
        self.mailbox.action_resume()
        self.assertTrue(self.mailbox.sync_enabled)
        self.assertEqual(self.mailbox.sync_failure_count, 0)
        self.assertEqual(self.mailbox.sync_status, 'syncing')
        bodies = self.mailbox.message_ids.mapped('body')
        self.assertEqual(sum('Sync paused' in (b or '') for b in bodies), 1)
        self.assertEqual(sum('Sync resumed' in (b or '') for b in bodies), 1)

    def test_resume_starts_a_stopped_sync(self):
        self.mailbox.write({'sync_enabled': False,
                            'last_check_date': fields.Datetime.now(),
                            'sync_failure_count': SYNC_FAILURE_LIMIT,
                            'error_message': '503 Service Unavailable'})
        self.assertEqual(self.mailbox.sync_status, 'stopped')
        self.assertIn('503', self.mailbox.sync_note)
        self.assertTrue(self.mailbox.sync_attention)
        self.mailbox.action_resume()
        self.assertTrue(self.mailbox.sync_enabled)
        self.assertFalse(self.mailbox.sync_note)

    def test_a_mailbox_the_cron_refuses_is_red_with_the_reason(self):
        """Setup not finished or not connected: no failed run, but the cron
        reads nothing, so the row is red with the cron's reason."""
        self.mailbox.write({'last_check_date': fields.Datetime.now(), 'state': 'error',
                            'error_message': 'Mail Pro is not set up yet.'})
        self.assertEqual(self.mailbox.sync_status, 'syncing')
        self.assertTrue(self.mailbox.sync_attention)
        self.assertEqual(self.mailbox.sync_value, 'Mail Pro is not set up yet.')
        self.assertEqual(self.mailbox.sync_note, 'Mail Pro is not set up yet.')

    def test_a_failed_run_is_not_a_state(self):
        """A failed run is red on the rows, the status stays Syncing."""
        self.mailbox.write({'last_check_date': fields.Datetime.now(),
                            'sync_failure_count': 1})
        self.assertEqual(self.mailbox.sync_status, 'syncing')
        self.assertTrue(self.mailbox.sync_attention)

    def test_never_syncing_before_anything_was_checked(self):
        with patch.object(type(self.mailbox), '_has_working_credentials',
                          return_value=True, autospec=True):
            self.mailbox.invalidate_recordset(['sync_status', 'health_status'])
            self.assertFalse(self.mailbox.last_check_date)
            self.assertEqual(self.mailbox.sync_status, 'not_started')
            self.mailbox.last_check_date = fields.Datetime.now()
            self.assertEqual(self.mailbox.sync_status, 'syncing')

    def test_a_run_means_it_started(self):
        self.assertEqual(self.mailbox.sync_status, 'not_started')
        self.Run.create({'mailbox_id': self.mailbox.id, 'state': 'done'})
        self.assertEqual(self.mailbox.sync_status, 'syncing')

    def test_routing_follows_the_team_picked(self):
        alias = self.env['mail.alias'].search([('alias_name', '!=', False)], limit=1)
        if not alias:
            self.skipTest('no alias with a name on this database')
        self.mailbox.alias_id = alias
        self.assertTrue(self.mailbox.route_to_team)
        self.mailbox.alias_id = False
        self.assertFalse(self.mailbox.route_to_team)

    def test_the_chatter_keeps_the_settings_history(self):
        """The settings a person changes are tracked in the chatter."""
        tracked = self.mailbox._track_get_fields()
        for name in ('sync_level', 'route_to_team', 'alias_id', 'is_notification_mailbox',
                     'owner_user_id', 'sync_start_date', 'sync_enabled'):
            self.assertIn(name, tracked, name)

    def test_the_sync_page_is_the_same_record_and_reads_sync(self):
        action = self.mailbox.action_open_sync()
        self.assertEqual(action['res_id'], self.mailbox.id)
        view = self.env.ref('pan_mail_pro.view_pan_mail_mailbox_sync_form')
        self.assertEqual(action['views'], [(view.id, 'form')])
        page = self.mailbox.with_context(**action['context'])
        self.assertEqual(page.display_name, 'Sync')
        self.assertEqual(self.mailbox.display_name, 'runs@company.test')
        arch = etree.fromstring(self.mailbox.get_view(view.id)['arch'])
        for name in ('sync_level', 'alias_id', 'sync_start_date', 'runs_value'):
            self.assertTrue(arch.xpath(f'//field[@name="{name}"]'), name)
        self.assertTrue(arch.xpath('//header//field[@name="sync_status"][@widget="statusbar"]'))
        for name in ('action_pause', 'action_resume'):
            self.assertTrue(arch.xpath(f'//header//button[@name="{name}"]'), name)
        # Runs is one row, a level of its own: no Last Run, Next Run or
        # Mail Log rows beside it, and no switch.
        self.assertFalse(arch.xpath('//field[@name="sync_enabled"]'))
        self.assertTrue(arch.xpath('//button[@name="action_open_runs"]'))
        self.assertTrue(arch.xpath('//chatter'))

    def test_the_mailbox_page_has_one_button_sign_in(self):
        """The only button in the header is Sign In, and only while the
        sign-in is missing. The rows are Owner and Sync; the term "Sign In
        as This Address" is on no screen."""
        view = self.env.ref('pan_mail_pro.view_pan_mail_mailbox_form')
        arch = etree.fromstring(self.mailbox.get_view(view.id)['arch'])
        buttons = arch.xpath('//header//button')
        self.assertEqual([b.get('name') for b in buttons], ['action_sign_in'])
        self.assertIn('can_sign_in', buttons[0].get('invisible'))
        self.assertEqual(len(arch.xpath('//button[contains(@class, "btn-primary")]')), 1)
        self.assertTrue(arch.xpath('//button[@name="action_open_sync"]'))
        # The status is the Sync page's, said once: no badge here.
        self.assertFalse(arch.xpath('//field[@name="sync_status"]'))
        for gone in ('action_open_routing_log', 'action_open_issues'):
            self.assertFalse(arch.xpath(f'//button[@name="{gone}"]'), gone)
        self.assertNotIn('as This Address', etree.tostring(arch).decode())
        self.assertTrue(arch.xpath('//chatter'))
        # The only run button is the Sync page's Run Now.
        self.assertFalse(arch.xpath('//button[@name="action_sync_now"]'))
        sync = etree.fromstring(self.mailbox.get_view(
            self.env.ref('pan_mail_pro.view_pan_mail_mailbox_sync_form').id)['arch'])
        self.assertTrue(sync.xpath('//button[@name="action_sync_now"][@string="Run Now"]'))

    def test_runs_are_vacuumed(self):
        now = fields.Datetime.now()
        empty_old = self.Run.create({'mailbox_id': self.mailbox.id, 'state': 'done',
                                     'started': now - timedelta(days=2)})
        busy_recent = self.Run.create({'mailbox_id': self.mailbox.id, 'state': 'done',
                                       'read_count': 3, 'started': now - timedelta(days=2)})
        busy_old = self.Run.create({'mailbox_id': self.mailbox.id, 'state': 'done',
                                    'read_count': 3, 'started': now - timedelta(days=31)})
        fresh = self.Run.create({'mailbox_id': self.mailbox.id, 'state': 'done'})
        self.Run._gc_runs()
        self.assertFalse(empty_old.exists())
        self.assertFalse(busy_old.exists())
        self.assertTrue(busy_recent.exists())
        self.assertTrue(fresh.exists())
