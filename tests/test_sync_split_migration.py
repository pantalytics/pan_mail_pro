# -*- coding: utf-8 -*-
"""The 19.0.7.6.0 mapping from `sync_mode` to the two switches.

CI upgrades from a release tag into a database with no mailboxes, so the
migration runs and reports "0 rows" every time. That proves it does not crash.
It does not prove it maps anything, and this is the one script that decides
whether a customer keeps reading the mail they read yesterday.

So the column is recreated here, real rows are seeded through it, and the script
is loaded and run against them. The ORM has no idea `sync_mode` exists any more,
which is why the fixture writes raw SQL: a mailbox created through `create()`
would leave the legacy column NULL and the migration would correctly do nothing.
"""
import importlib.util
import os

from odoo.tests import tagged

from .common import MailProTestCase

_MIGRATIONS = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'migrations',
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
class TestSyncSplitMigration(MailProTestCase):

    # sync_mode → (sync_received, sync_received_scope, sync_sent)
    EXPECTED = {
        'none': (False, 'known_partners', False),
        'known_partners': (True, 'known_partners', True),
        'all': (True, 'all', True),
    }

    def setUp(self):
        super().setUp()
        self.pre_migrate = _load('19.0.7.6.0', 'pre-migrate', 'pan_sync_split_pre')
        self.post_migrate = _load('19.0.7.6.0', 'post-migrate', 'pan_sync_split_post')
        self.cr = self.env.cr
        self.cr.execute(
            'ALTER TABLE pan_mail_mailbox ADD COLUMN IF NOT EXISTS sync_mode varchar'
        )
        # `sync_received_scope` is required, so the loaded schema carries a NOT
        # NULL the fixture has to get under to reproduce a pre-upgrade row. The
        # test transaction is rolled back, so the constraint comes straight
        # back; in production it is added by the ORM at load, which is after
        # pre-migrate has filled the column.
        self.cr.execute(
            'ALTER TABLE pan_mail_mailbox '
            'ALTER COLUMN sync_received_scope DROP NOT NULL'
        )

    def _legacy_mailbox(self, email, sync_mode):
        """A mailbox as 19.0.7.4.0 left it: the old column set, the new ones NULL."""
        mailbox = self.env['pan.mail.mailbox'].create({
            'email': email,
            'mailbox_type': 'shared',
        })
        self.cr.execute(
            """
            UPDATE pan_mail_mailbox
               SET sync_mode = %s,
                   sync_received = NULL,
                   sync_received_scope = NULL,
                   sync_sent = NULL
             WHERE id = %s
            """,
            (sync_mode, mailbox.id),
        )
        mailbox.invalidate_recordset()
        return mailbox

    def _read(self, mailbox):
        self.cr.execute(
            'SELECT sync_received, sync_received_scope, sync_sent '
            '  FROM pan_mail_mailbox WHERE id = %s',
            (mailbox.id,),
        )
        return self.cr.fetchone()

    def test_every_mode_maps_to_the_same_behaviour_it_had(self):
        """Whatever a mailbox read yesterday, it reads today."""
        mailboxes = {
            mode: self._legacy_mailbox('mode-%s@company.test' % mode, mode)
            for mode in self.EXPECTED
        }

        self.pre_migrate.migrate(self.cr, '19.0.7.4.0')

        for mode, expected in self.EXPECTED.items():
            self.assertEqual(
                self._read(mailboxes[mode]), expected,
                "sync_mode=%r must keep reading exactly what it read before" % mode,
            )

    def test_a_second_run_changes_nothing(self):
        """An upgrade that is re-run must not walk a mailbox's settings back to
        whatever the legacy column still says."""
        mailbox = self._legacy_mailbox('rerun@company.test', 'all')

        self.pre_migrate.migrate(self.cr, '19.0.7.4.0')
        # Somebody narrows the scope after the upgrade, the way a customer would.
        # Written in SQL rather than through the ORM so the assertion is about
        # the migration and not about a mailbox constraint firing on the way in.
        self.cr.execute(
            """
            UPDATE pan_mail_mailbox
               SET sync_received_scope = 'known_partners', sync_sent = FALSE
             WHERE id = %s
            """,
            (mailbox.id,),
        )

        self.pre_migrate.migrate(self.cr, '19.0.7.4.0')

        self.assertEqual(
            self._read(mailbox), (True, 'known_partners', False),
            "a re-run must leave a choice made after the upgrade alone",
        )

    def test_a_fresh_install_is_left_alone(self):
        """`version` is falsy on an install rather than an upgrade."""
        mailbox = self._legacy_mailbox('fresh@company.test', 'all')

        self.pre_migrate.migrate(self.cr, None)

        self.assertEqual(self._read(mailbox), (None, None, None))

    def test_the_legacy_column_is_dropped_afterwards(self):
        """Left behind, it is a second answer to a question that now has one."""
        self.post_migrate.migrate(self.cr, '19.0.7.4.0')

        self.cr.execute("""
            SELECT 1 FROM information_schema.columns
             WHERE table_name = 'pan_mail_mailbox' AND column_name = 'sync_mode'
        """)
        self.assertIsNone(self.cr.fetchone())
