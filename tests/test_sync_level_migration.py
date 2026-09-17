# -*- coding: utf-8 -*-
"""The 19.0.8.0.0 fold of two switches and a scope into one ladder.

CI upgrades from a release tag into a database with no mailboxes, so the
migration runs and reports "0 rows" every time. That proves it does not crash.
It does not prove it maps anything, and this is the script that decides what a
customer keeps reading after the upgrade.

So the three legacy columns are recreated here, real rows are seeded through
them, and the script is loaded and run against them. The ORM has no idea the
old columns exist any more, which is why the fixture writes raw SQL. The chain
from 19.0.7.4.0 is covered too: `sync_mode` → the three switches → the ladder,
the way a database that skips 19.0.7.6.0 meets both scripts in one upgrade.
"""
import importlib.util
import os

from odoo.tests import tagged

from .common import MailProTestCase

_MIGRATIONS = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'migrations',
)

LEGACY = (
    ('sync_received', 'boolean'),
    ('sync_received_scope', 'varchar'),
    ('sync_sent', 'boolean'),
)


def _load(version, phase, name):
    """Load a migration script by version and phase.

    Fails with FileNotFoundError rather than skipping: a migration test that
    quietly stops finding its script is a test that proves nothing.
    """
    path = os.path.join(_MIGRATIONS, version, '%s.py' % phase)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestSyncLevelMigration(MailProTestCase):

    # (sync_received, sync_received_scope, sync_sent) → sync_level
    EXPECTED = {
        (False, 'known_partners', False): 'replies',
        (False, 'known_partners', True): 'both',
        (True, 'known_partners', False): 'contacts',
        (True, 'known_partners', True): 'contacts',
        (True, 'all', False): 'everyone',
        (True, 'all', True): 'everyone',
        # A row predating 19.0.7.6.0's defaults: NULL falls the safe way.
        (None, None, None): 'replies',
    }

    def setUp(self):
        super().setUp()
        self.pre_migrate = _load('19.0.8.0.0', 'pre-migrate', 'pan_sync_level_pre')
        self.post_migrate = _load('19.0.8.0.0', 'post-migrate', 'pan_sync_level_post')
        self.cr = self.env.cr
        for name, sql_type in LEGACY:
            self.cr.execute(
                'ALTER TABLE pan_mail_mailbox ADD COLUMN IF NOT EXISTS %s %s'
                % (name, sql_type)
            )
        # `sync_level` is required, so the loaded schema carries a NOT NULL the
        # fixture has to get under to reproduce a pre-upgrade row. The test
        # transaction is rolled back, so the constraint comes straight back; in
        # production it is added by the ORM at load, after pre-migrate ran.
        self.cr.execute(
            'ALTER TABLE pan_mail_mailbox ALTER COLUMN sync_level DROP NOT NULL'
        )

    def _legacy_mailbox(self, email, received, scope, sent):
        """A mailbox as 19.0.7.x left it: the three switches set, the ladder NULL."""
        mailbox = self.env['pan.mail.mailbox'].create({
            'email': email,
        })
        self.cr.execute(
            """
            UPDATE pan_mail_mailbox
               SET sync_received = %s,
                   sync_received_scope = %s,
                   sync_sent = %s,
                   sync_level = NULL
             WHERE id = %s
            """,
            (received, scope, sent, mailbox.id),
        )
        mailbox.invalidate_recordset()
        return mailbox

    def _level(self, mailbox):
        self.cr.execute(
            'SELECT sync_level FROM pan_mail_mailbox WHERE id = %s', (mailbox.id,),
        )
        return self.cr.fetchone()[0]

    def test_every_combination_lands_on_its_rung(self):
        mailboxes = {
            combo: self._legacy_mailbox('combo-%d@company.test' % i, *combo)
            for i, combo in enumerate(self.EXPECTED)
        }

        self.pre_migrate.migrate(self.cr, '19.0.7.8.1')

        for combo, expected in self.EXPECTED.items():
            self.assertEqual(
                self._level(mailboxes[combo]), expected,
                "%r must land on %r" % (combo, expected),
            )

    def test_a_second_run_changes_nothing(self):
        """An upgrade that is re-run must not walk a mailbox's choice back to
        whatever the legacy columns still say."""
        mailbox = self._legacy_mailbox('rerun@company.test', True, 'all', True)

        self.pre_migrate.migrate(self.cr, '19.0.7.8.1')
        # Somebody narrows the level after the upgrade, the way a customer would.
        self.cr.execute(
            "UPDATE pan_mail_mailbox SET sync_level = 'replies' WHERE id = %s",
            (mailbox.id,),
        )

        self.pre_migrate.migrate(self.cr, '19.0.7.8.1')

        self.assertEqual(self._level(mailbox), 'replies')

    def test_a_fresh_install_is_left_alone(self):
        """`version` is falsy on an install rather than an upgrade."""
        mailbox = self._legacy_mailbox('fresh@company.test', True, 'all', True)

        self.pre_migrate.migrate(self.cr, None)

        self.assertIsNone(self._level(mailbox))

    def test_the_chain_from_sync_mode_reaches_the_ladder(self):
        """A database on 19.0.7.4.0 meets both scripts in one upgrade: the
        7.6.0 split runs first and fills the three switches, then this fold
        reads them. Every `sync_mode` has to come out on the rung it meant."""
        split = _load('19.0.7.6.0', 'pre-migrate', 'pan_sync_split_pre')
        self.cr.execute(
            'ALTER TABLE pan_mail_mailbox ADD COLUMN IF NOT EXISTS sync_mode varchar'
        )
        expected = {'none': 'replies', 'known_partners': 'contacts', 'all': 'everyone'}
        mailboxes = {}
        for mode in expected:
            mailbox = self._legacy_mailbox('mode-%s@company.test' % mode, None, None, None)
            self.cr.execute(
                'UPDATE pan_mail_mailbox SET sync_mode = %s WHERE id = %s',
                (mode, mailbox.id),
            )
            mailboxes[mode] = mailbox

        split.migrate(self.cr, '19.0.7.4.0')
        self.pre_migrate.migrate(self.cr, '19.0.7.4.0')

        for mode, level in expected.items():
            self.assertEqual(self._level(mailboxes[mode]), level, mode)

    def test_the_legacy_columns_are_dropped_afterwards(self):
        """Left behind, they are a second answer to a question that now has one."""
        self.post_migrate.migrate(self.cr, '19.0.7.8.1')

        self.cr.execute("""
            SELECT column_name FROM information_schema.columns
             WHERE table_name = 'pan_mail_mailbox'
               AND column_name IN ('sync_received', 'sync_received_scope', 'sync_sent')
        """)
        self.assertEqual(self.cr.fetchall(), [])
