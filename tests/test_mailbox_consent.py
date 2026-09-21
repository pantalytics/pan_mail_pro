# -*- coding: utf-8 -*-
"""Who decides how much of a mailbox Odoo reads.

The sync level decides how much of somebody's correspondence ends up on
records their colleagues can open, so on a personal mailbox it belongs to its
owner. An administrator keeps every way of *stopping* a sync and none of
starting one: lowering, archiving and disconnecting expose nothing, raising is
the only direction that does.

Three exemptions, each asserted below because each one is a hole if it is
wrong: `sudo()` (the OAuth callback claiming a mailbox, and the crons), a
shared mailbox (the company's, nobody is asked) and the notification mailbox
(personal by type, system mail by content).
"""
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import MailProTestCase


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestMailboxConsent(MailProTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Privileged and not the superuser: a mailbox manager by way of
        # group_system, which is the actor this guard exists for.
        cls.admin = cls.env.ref('base.user_admin')

    # -- the guard ---------------------------------------------------------

    def _as_owner(self, level, user=None, mailbox=None):
        """Raise a level the way an owner actually can.

        Not `mailbox.write()`: an internal user has *read* on
        `pan.mail.mailbox` and nothing else, so the owner's own route is the
        field on their user record, which writes the mailbox under sudo after
        checking whose it is. A test that wrote the mailbox directly would be
        testing an actor that cannot exist.
        """
        user = user or self.salesperson
        user.with_user(user).write({'x_pan_mail_sync_level': level})
        return mailbox or self.personal_mailbox

    def test_owner_may_raise_and_the_choice_is_dated(self):
        self._as_owner('contacts')
        self.assertEqual(self.personal_mailbox.sync_level, 'contacts')
        self.assertTrue(self.personal_mailbox.consent_date,
                        'the owner chose, and nothing recorded when')

    def test_a_manager_may_not_raise_somebody_elses_level(self):
        before = self.personal_mailbox.sync_level
        with self.assertRaises(AccessError):
            self.personal_mailbox.with_user(self.admin).write(
                {'sync_level': 'everyone'})
        self.assertEqual(self.personal_mailbox.sync_level, before)

    def test_a_manager_may_always_lower(self):
        self._as_owner('everyone')
        dated = self.personal_mailbox.consent_date
        self.personal_mailbox.with_user(self.admin).write(
            {'sync_level': 'replies'})
        self.assertEqual(self.personal_mailbox.sync_level, 'replies')
        self.assertEqual(
            self.personal_mailbox.consent_date, dated,
            'lowering is not consent and must not re-date the column')

    def test_sudo_passes(self):
        """The OAuth callback writes the first level for a person."""
        self.personal_mailbox.sudo().write({'sync_level': 'everyone'})
        self.assertEqual(self.personal_mailbox.sync_level, 'everyone')

    def test_a_shared_mailbox_is_the_administrators(self):
        # A shared mailbox that reads needs somebody's credentials to read
        # with; that is a different rule (see `_check_owner_required`) and not
        # what this asserts.
        self.shared_mailbox.sudo().owner_user_id = self.notif_owner
        self.shared_mailbox.with_user(self.admin).write({'sync_level': 'everyone'})
        self.assertEqual(self.shared_mailbox.sync_level, 'everyone')
        self.assertFalse(self.shared_mailbox.consent_date,
                         'nobody is asked about a shared mailbox')

    def test_the_notification_mailbox_is_the_administrators(self):
        """Personal by type, the company's system mail by content."""
        self.notification_mailbox.with_user(self.admin).write(
            {'sync_level': 'contacts'})
        self.assertEqual(self.notification_mailbox.sync_level, 'contacts')

    def test_a_manager_may_not_create_one_for_somebody_else_either(self):
        """The same rule on the door beside it.

        A guard on write alone is a guard you walk around by creating the
        mailbox with the level already on it.
        """
        with self.assertRaises(AccessError):
            self.env['pan.mail.mailbox'].with_user(self.admin).create({
                # An address its owner holds credentials for, which is what
                # makes the mailbox personal rather than shared.
                'email': self.other_user.email,
                'provider': 'outlook',
                'owner_user_id': self.other_user.id,
                'sync_level': 'everyone',
            })

    # -- the user's own screen ---------------------------------------------

    def test_a_user_sets_their_own_level_from_preferences(self):
        """My Preferences is the only screen they have, and it must work.

        An internal user has read and no write on `pan.mail.mailbox`, so the
        level reaches the mailbox through this field or not at all.
        """
        user = self.salesperson.with_user(self.salesperson)
        self.assertEqual(user.x_pan_mail_sync_level,
                         self.personal_mailbox.sync_level)
        user.write({'x_pan_mail_sync_level': 'contacts'})
        self.assertEqual(self.personal_mailbox.sync_level, 'contacts')
        self.assertTrue(self.personal_mailbox.consent_date)

    def test_nobody_sets_it_for_somebody_else(self):
        with self.assertRaises(AccessError):
            self.other_user.with_user(self.salesperson).write(
                {'x_pan_mail_sync_level': 'everyone'})

    def test_only_the_mailbox_on_the_screen_is_raised(self):
        """One level shown, one mailbox written.

        The field shows the first personal mailbox its owner has. Writing
        every one of them would raise a mailbox nobody was shown -- under
        sudo, so the guard would not stop it either.
        """
        # Personal too, and without a second account: the owner's own login
        # address counts as theirs, which is what `_compute_mailbox_type` asks.
        second = self.env['pan.mail.mailbox'].sudo().create({
            'email': self.salesperson.email,
            'provider': 'outlook',
            'owner_user_id': self.salesperson.id,
            'sequence': 99,
        })
        self.assertEqual(second.mailbox_type, 'personal')

        self._as_owner('everyone')

        self.assertEqual(self.personal_mailbox.sync_level, 'everyone')
        self.assertEqual(second.sync_level, 'replies',
                         'a mailbox nobody was shown must not be raised')

    def test_a_shared_mailbox_is_not_somebodys_own(self):
        """The field answers for personal mailboxes and no others.

        `other_user` owns nothing personal, and the notification mailbox's
        owner must not find their system address on their own screen.
        """
        self.assertFalse(self.other_user.x_pan_mail_sync_level)
        self.assertFalse(self.notif_owner.x_pan_mail_sync_level)
