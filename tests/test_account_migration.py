# -*- coding: utf-8 -*-
"""Coverage for the pan.mail.account token migration.

Phase 1 taught this the hard way: a green suite is not coverage. The migration
is the highest-risk code in the whole refactor - it runs once, against real
encrypted tokens, and a column typo would not surface until the next send. So
the script is loaded and executed here against real rows rather than trusted.

What this cannot test is a *production* token population: expired tokens, users
whose partner email drifted from their login, tokens encrypted before a key
rotation. REFACTOR_PHASE2.md's rehearsal against a restored backup stays
mandatory.
"""
import importlib.util
import os

from odoo.tests import TransactionCase, tagged

# Pinned to the version directory on purpose. When a later phase adds another
# migration, this raises FileNotFoundError instead of silently testing nothing.
_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..', 'migrations', '19.0.1.2.0', 'post-migrate.py',
)


def _load_migration():
    spec = importlib.util.spec_from_file_location('pan_account_post_migrate', _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tagged('pan_outlook_pro', 'post_install', '-at_install')
class TestAccountMigration(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.migration = _load_migration()
        cls.Account = cls.env['pan.mail.account']

    def _run_migration(self):
        """Run the script the way Odoo would: raw cursor, no ORM cache."""
        self.env.flush_all()
        self.migration.migrate(self.env.cr, '19.0.1.1.1')
        self.env.invalidate_all()

    def _connected_user(self, login, email=None):
        return self.env['res.users'].create({
            'name': login,
            'login': login,
            'email': email,
            'x_microsoft_access_token': 'access-for-%s' % login,
            'x_microsoft_refresh_token': 'refresh-for-%s' % login,
        })

    def _account_of(self, user):
        return self.Account.with_context(active_test=False).search([
            ('user_id', '=', user.id), ('provider', '=', 'microsoft'),
        ])

    def test_connected_user_gets_an_account(self):
        user = self._connected_user('migrate_me@test.local', email='migrate_me@test.local')

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
        user = self._connected_user('cipher@test.local', email='cipher@test.local')
        original_refresh = user.x_microsoft_refresh_token_encrypted
        original_access = user.x_microsoft_access_token_encrypted

        self._run_migration()

        account = self._account_of(user)
        self.assertEqual(account.refresh_token_encrypted, original_refresh)
        self.assertEqual(account.access_token_encrypted, original_access)
        self.assertEqual(account.refresh_token, 'refresh-for-cipher@test.local')
        self.assertEqual(account.access_token, 'access-for-cipher@test.local')

    def test_user_columns_survive_as_the_rollback(self):
        user = self._connected_user('rollback@test.local', email='rollback@test.local')

        self._run_migration()

        self.assertTrue(user.x_microsoft_refresh_token_encrypted)
        self.assertTrue(user.x_microsoft_oauth_connected)

    def test_user_without_tokens_gets_no_account(self):
        user = self.env['res.users'].create({
            'name': 'Never Connected',
            'login': 'never_connected@test.local',
            'email': 'never_connected@test.local',
        })

        self._run_migration()

        self.assertFalse(self._account_of(user))

    def test_running_twice_does_not_duplicate(self):
        """A half-finished upgrade has to be retryable."""
        user = self._connected_user('twice@test.local', email='twice@test.local')

        self._run_migration()
        self._run_migration()

        self.assertEqual(len(self._account_of(user)), 1)

    def test_email_falls_back_to_login(self):
        """email is NOT NULL, and a user with no partner email is not exotic."""
        user = self._connected_user('no_partner_email@test.local', email=False)

        self._run_migration()

        self.assertEqual(self._account_of(user).email, 'no_partner_email@test.local')

    def test_archived_user_keeps_an_archived_account(self):
        # Archived after creation, not during: auth_signup refuses to send the
        # welcome mail to an archived user and raises out of create().
        user = self._connected_user('archived@test.local', email='archived@test.local')
        user.active = False

        self._run_migration()

        account = self._account_of(user)
        self.assertEqual(len(account), 1)
        self.assertFalse(account.active)
