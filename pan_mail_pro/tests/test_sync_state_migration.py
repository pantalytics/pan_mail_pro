# -*- coding: utf-8 -*-
"""19.0.43.0.0 moves the sync's dates off the mailbox row (#473).

Run against a database shaped the way the previous version left it: the three
columns on `pan_mail_mailbox`, which a fresh install no longer has, added here
and rolled back with the test.
"""
import importlib.util
import os
from datetime import datetime

from odoo.tests import tagged

from .common import MailProTestCase

_SCRIPT = os.path.join(os.path.dirname(__file__), '..', 'migrations',
                       '19.0.43.0.0', 'post-migrate.py')


def _load():
    spec = importlib.util.spec_from_file_location('sync_state_migration', _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestSyncStateMigration(MailProTestCase):

    def setUp(self):
        super().setUp()
        self.env.cr.execute("""
            ALTER TABLE pan_mail_mailbox
                ADD COLUMN IF NOT EXISTS last_sync_date timestamp,
                ADD COLUMN IF NOT EXISTS last_sent_sync_date timestamp,
                ADD COLUMN IF NOT EXISTS last_check_date timestamp
        """)
        self.mailbox = self.personal_mailbox
        self.env['pan.mail.sync.state'].search([]).unlink()
        self.env.flush_all()
        self.env.cr.execute("""
            UPDATE pan_mail_mailbox
               SET last_sync_date = %s, last_sent_sync_date = %s, last_check_date = %s
             WHERE id = %s
        """, [datetime(2026, 9, 8, 8, 0), datetime(2026, 9, 7, 8, 0),
              datetime(2026, 11, 27, 9, 0), self.mailbox.id])

    def _migrate(self):
        _load().migrate(self.env.cr, '19.0.42.2.0')
        self.env.invalidate_all()

    def test_every_mailbox_gets_its_dates(self):
        self._migrate()
        self.assertEqual(self.mailbox.last_sync_date, datetime(2026, 9, 8, 8, 0))
        self.assertEqual(self.mailbox.last_sent_sync_date, datetime(2026, 9, 7, 8, 0))
        self.assertEqual(self.mailbox.last_check_date, datetime(2026, 11, 27, 9, 0))
        mailboxes = self.env['pan.mail.mailbox'].with_context(active_test=False).search([])
        self.assertEqual(
            set(self.env['pan.mail.sync.state'].search([]).mailbox_id.ids),
            set(mailboxes.ids), 'a mailbox was left without a state row')

    def test_running_it_twice_changes_nothing(self):
        self._migrate()
        count = self.env['pan.mail.sync.state'].search_count([])
        self._migrate()
        self.assertEqual(self.env['pan.mail.sync.state'].search_count([]), count)
