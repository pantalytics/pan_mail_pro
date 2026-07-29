# -*- coding: utf-8 -*-
"""Coverage for the pan.mail.account token migration.

Phase 1 taught this the hard way: a green suite is not coverage. The migration
is the highest-risk code in the whole refactor - it runs once, against real
encrypted tokens, and a column typo would not surface until the next send. So
the script is loaded and executed here against real rows rather than trusted.

The fixture writes tokens into the res_users columns with raw SQL, because that
is the only way to reproduce a pre-migration database now that the ORM fields
are proxies onto the account. Creating a user through the ORM would create the
account too, and the migration would correctly do nothing - a green test that
proves nothing.

The columns themselves have to be recreated first. They only survive on a
database that was upgraded from <= 19.0.1.1.1; on a fresh install Odoo never
creates them, because the fields are store=False. A developer database has them
and CI does not, which is exactly the kind of split that hides a broken
migration until release day - so the fixture creates them when absent instead of
assuming either shape.

What this cannot test is a *production* token population: expired tokens, users
whose partner email drifted from their login, tokens encrypted before a key
rotation. REFACTOR_PHASE2.md's rehearsal against a restored backup stays
mandatory.
"""
import importlib.util
import os

from odoo.tests import TransactionCase, tagged

from odoo.addons.pan_mail_pro.models import encryption_utils

# Pinned to the version directory on purpose. When a later phase adds another
# migration, this raises FileNotFoundError instead of silently testing nothing.
_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..', 'migrations', '19.0.2.1.0', 'post-migrate.py',
)


def _load_migration():
    spec = importlib.util.spec_from_file_location('pan_account_post_migrate', _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestAccountMigration(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.migration = _load_migration()
        cls.Account = cls.env['pan.mail.account']
        cls._ensure_legacy_columns()

    @classmethod
    def _ensure_legacy_columns(cls):
        """Give the test database the pre-migration res_users shape.

        Types match what Odoo generated when the fields were still stored
        (Char -> varchar, Datetime -> timestamp). TransactionCase rolls the DDL
        back with everything else, so an upgraded database is left untouched and
        a fresh one does not keep the columns.
        """
        cls.env.cr.execute("""
            ALTER TABLE res_users
                ADD COLUMN IF NOT EXISTS x_microsoft_access_token_encrypted varchar,
                ADD COLUMN IF NOT EXISTS x_microsoft_refresh_token_encrypted varchar,
                ADD COLUMN IF NOT EXISTS x_microsoft_token_expiry timestamp
        """)

    def _run_migration(self):
        """Run the script the way Odoo would: raw cursor, no ORM cache."""
        self.env.flush_all()
        self.migration.migrate(self.env.cr, '19.0.1.1.1')
        self.env.invalidate_all()

    def _legacy_user(self, login, email=None, tokens=True, connected=None):
        """A user as the pre-19.0.2.1.0 code would have left it: tokens in the
        res_users columns, no pan.mail.account anywhere."""
        user = self.env['res.users'].create({'name': login, 'login': login, 'email': email})
        self.env.flush_all()

        access = refresh = None
        if tokens:
            access = encryption_utils.encrypt_value(self.env, 'access-for-%s' % login)
            refresh = encryption_utils.encrypt_value(self.env, 'refresh-for-%s' % login)
        if connected is None:
            connected = tokens

        self.env.cr.execute("""
            UPDATE res_users
               SET x_microsoft_access_token_encrypted = %s,
                   x_microsoft_refresh_token_encrypted = %s,
                   x_microsoft_token_expiry = %s,
                   x_microsoft_oauth_connected = %s
             WHERE id = %s
        """, (access, refresh, '2026-01-01 12:00:00' if tokens else None, connected, user.id))
        self.env.invalidate_all()
        return user

    def _account_of(self, user):
        return self.Account.with_context(active_test=False).search([
            ('user_id', '=', user.id), ('provider', '=', 'outlook'),
        ])

    def _column(self, user, column):
        self.env.cr.execute(
            "SELECT %s FROM res_users WHERE id = %%s" % column, (user.id,))
        return self.env.cr.fetchone()[0]

    def test_connected_user_gets_an_account(self):
        user = self._legacy_user('migrate_me@test.local', email='migrate_me@test.local')

        self._run_migration()

        account = self._account_of(user)
        self.assertEqual(len(account), 1)
        self.assertEqual(account.email, 'migrate_me@test.local')
        self.assertTrue(account.connected)
        self.assertTrue(account.active)

    def test_ciphertext_is_copied_not_re_encrypted(self):
        """Same key, same DB - moving the encrypted string cannot fail halfway.

        Asserting on the ciphertext, not just the decrypted value: a re-encrypt
        would still decrypt correctly here and still be the wrong thing to ship.
        """
        user = self._legacy_user('cipher@test.local', email='cipher@test.local')
        original_refresh = self._column(user, 'x_microsoft_refresh_token_encrypted')
        original_access = self._column(user, 'x_microsoft_access_token_encrypted')

        self._run_migration()

        account = self._account_of(user)
        self.assertEqual(account.refresh_token_encrypted, original_refresh)
        self.assertEqual(account.access_token_encrypted, original_access)
        self.assertEqual(account.refresh_token, 'refresh-for-cipher@test.local')
        self.assertEqual(account.access_token, 'access-for-cipher@test.local')

    def test_user_columns_survive_as_the_rollback(self):
        """Copy, do not move. The columns are how this release is undone."""
        user = self._legacy_user('rollback@test.local', email='rollback@test.local')

        self._run_migration()

        self.assertTrue(self._column(user, 'x_microsoft_refresh_token_encrypted'))
        self.assertTrue(self._column(user, 'x_microsoft_access_token_encrypted'))

    def test_user_without_tokens_gets_no_account(self):
        user = self._legacy_user('never_connected@test.local',
                                 email='never_connected@test.local', tokens=False)

        self._run_migration()

        self.assertFalse(self._account_of(user))

    def test_running_twice_does_not_duplicate(self):
        """A half-finished upgrade has to be retryable."""
        user = self._legacy_user('twice@test.local', email='twice@test.local')

        self._run_migration()
        self._run_migration()

        self.assertEqual(len(self._account_of(user)), 1)

    def test_email_falls_back_to_login(self):
        """email is NOT NULL, and a user with no partner email is not exotic."""
        user = self._legacy_user('no_partner_email@test.local', email=False)

        self._run_migration()

        self.assertEqual(self._account_of(user).email, 'no_partner_email@test.local')

    def test_archived_user_keeps_an_archived_account(self):
        user = self._legacy_user('archived@test.local', email='archived@test.local')
        user.active = False

        self._run_migration()

        account = self._account_of(user)
        self.assertEqual(len(account), 1)
        self.assertFalse(account.active)

    def test_connection_flag_is_realigned_with_the_accounts(self):
        """The stored-compute trap, both directions.

        x_microsoft_oauth_connected keeps its old stored value across an upgrade
        even though its depends now points at the account. The migration has to
        realign it, or the mailbox owner dropdown lies.
        """
        stale_false = self._legacy_user('stale_false@test.local', connected=False)
        stale_true = self._legacy_user('stale_true@test.local', tokens=False, connected=True)

        self._run_migration()

        self.assertTrue(stale_false.x_microsoft_oauth_connected)
        self.assertFalse(stale_true.x_microsoft_oauth_connected)
