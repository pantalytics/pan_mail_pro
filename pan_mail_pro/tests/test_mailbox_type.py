# -*- coding: utf-8 -*-
"""Two questions about a mailbox, and the one setting beside them.

`mailbox_type` is the sending policy: an owner on their own address makes a
personal mailbox, no owner or an owner on a different address (whose token
reads it, on Microsoft) a shared one, and the notification mailbox is
personal whatever its address. It is never shown.

`provider_type` is the Type on screen: what Microsoft 365 or Google says the
mailbox is, Personal or Shared, with the owner rule standing in until the
first check and the notification flag never counting (#371).

Notification is exactly one mailbox: ticking one moves it, unticking the
last is refused.
"""
from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.pan_mail_pro.models.mail_provider_client import (
    ADDRESS_KIND_TYPES,
    ADDRESS_KINDS,
)

from .common import MailProTestCase


@tagged('post_install', '-at_install', 'pan_mail_pro')
class TestMailboxTypeIsDerived(MailProTestCase):

    def test_fixture_types_follow_from_their_owners(self):
        self.assertEqual(self.personal_mailbox.mailbox_type, 'personal')
        self.assertEqual(self.shared_mailbox.mailbox_type, 'shared')
        self.assertEqual(self.notification_mailbox.mailbox_type, 'personal')

    def test_no_owner_is_shared(self):
        mailbox = self.env['pan.mail.mailbox'].create({'email': 'team@company.test'})
        self.assertEqual(mailbox.mailbox_type, 'shared')

    def test_owner_on_another_address_is_shared(self):
        """The Microsoft shape: a person's token reads the team's address."""
        mailbox = self.env['pan.mail.mailbox'].create({
            'email': 'support@company.test',
            'owner_user_id': self.salesperson.id,
        })
        self.assertEqual(mailbox.mailbox_type, 'shared')
        self.assertTrue(mailbox._is_sendable_by(self.other_user))

    def test_owner_on_their_own_address_is_personal(self):
        """Their own address is the one their grant carries, or the one on
        their user record, in any case."""
        by_grant = self.env['pan.mail.mailbox'].create({
            'email': 'Other@Test.local',
            'owner_user_id': self.other_user.id,
        })
        self.assertEqual(by_grant.mailbox_type, 'personal')
        self.assertFalse(by_grant._is_sendable_by(self.salesperson))

        unconnected = self._silent('res.users').create({
            'name': 'Not Yet', 'login': 'notyet@test.local', 'email': 'notyet@test.local',
        })
        by_record = self.env['pan.mail.mailbox'].create({
            'email': 'notyet@test.local',
            'owner_user_id': unconnected.id,
        })
        self.assertEqual(by_record.mailbox_type, 'personal')

    def test_type_follows_the_owner_when_it_changes(self):
        """Both legs of the rule are live: the owner's grant and the owner."""
        mailbox = self.env['pan.mail.mailbox'].create({
            'email': 'support@company.test',
            'owner_user_id': self.other_user.id,
        })
        self.assertEqual(mailbox.mailbox_type, 'shared')
        self.other_user.x_pan_mail_account_ids.write({'email': 'support@company.test'})
        self.assertEqual(mailbox.mailbox_type, 'personal')
        mailbox.owner_user_id = False
        self.assertEqual(mailbox.mailbox_type, 'shared')

    def test_a_value_passed_in_does_not_stick(self):
        """Callers that still say the word are told what it is."""
        mailbox = self.env['pan.mail.mailbox'].create({
            'email': 'team@company.test', 'mailbox_type': 'personal',
        })
        self.assertEqual(mailbox.mailbox_type, 'shared')

    def test_the_connect_flow_yields_a_personal_mailbox(self):
        """What `_claim_personal_mailbox` creates: the address the provider
        just reported, on the user who authorized it."""
        user = self._silent('res.users').create({
            'name': 'New Hire', 'login': 'hire@test.local', 'email': 'hire@test.local',
        })
        self.connect(user, email='hire@company.test')
        mailbox = self.env['pan.mail.mailbox'].create({
            'email': 'hire@company.test', 'provider': 'outlook',
            'owner_user_id': user.id,
        })
        self.assertEqual(mailbox.mailbox_type, 'personal')


@tagged('post_install', '-at_install', 'pan_mail_pro')
class TestTypeIsTheProviders(MailProTestCase):

    def test_the_mapping(self):
        """Two values on screen, in one place, and every kind accounted for."""
        self.assertEqual(ADDRESS_KIND_TYPES, {
            'user': 'personal', 'alias': 'personal',
            'shared': 'shared', 'resource': 'shared',
        })
        self.assertLessEqual(set(ADDRESS_KIND_TYPES), {k for k, _label in ADDRESS_KINDS})

    def test_each_kind_once_checked(self):
        mailbox = self.personal_mailbox
        for kind, _label in ADDRESS_KINDS:
            mailbox.write({'address_kind': kind,
                           'access_checked_date': fields.Datetime.now()})
            expected = ADDRESS_KIND_TYPES.get(kind) or (
                'personal' if kind == 'unknown' else False)
            self.assertEqual(mailbox.provider_type, expected, 'address_kind %s' % kind)

    def test_the_owner_rule_stands_in_before_the_first_check(self):
        self.assertEqual(self.personal_mailbox.provider_type, 'personal')
        self.assertEqual(self.shared_mailbox.provider_type, 'shared')

    def test_the_notification_flag_never_counts(self):
        """The case seen on 2026-11-02: notifications@ read Personal."""
        mailbox = self.notification_mailbox
        self.assertEqual(mailbox.mailbox_type, 'personal')
        self.assertEqual(mailbox.provider_type, 'shared')
        mailbox.write({'address_kind': 'shared',
                       'access_checked_date': fields.Datetime.now()})
        self.assertEqual(mailbox.provider_type, 'shared')
        self.assertIn('Shared mailbox', mailbox.subtitle)

    def test_the_provider_wins_over_the_owner_rule(self):
        """A person's mailbox owned in Odoo by somebody else is still Personal."""
        mailbox = self.env['pan.mail.mailbox'].create({
            'email': 'support@company.test',
            'owner_user_id': self.salesperson.id,
        })
        self.assertEqual(mailbox.provider_type, 'shared')
        mailbox.write({'address_kind': 'user',
                       'access_checked_date': fields.Datetime.now()})
        self.assertEqual(mailbox.provider_type, 'personal')
        self.assertEqual(mailbox.mailbox_type, 'shared')

    def test_checked_without_the_scope_keeps_the_owner_rule(self):
        """A grant without MailboxSettings.Read leaves the kind unknown after
        the check; the Type must not go blank for an own-app customer."""
        mailbox = self.personal_mailbox
        mailbox.write({'address_kind': 'unknown',
                       'access_checked_date': fields.Datetime.now()})
        self.assertEqual(mailbox.provider_type, 'personal')
        self.assertEqual(self.shared_mailbox.provider_type, 'shared')

    def test_an_imap_login_or_no_mailbox_is_empty(self):
        mailbox = self.personal_mailbox
        for kind in ('account', 'none'):
            mailbox.address_kind = kind
            self.assertFalse(mailbox.provider_type, kind)
            self.assertNotIn('Personal mailbox', mailbox.subtitle)


@tagged('post_install', '-at_install', 'pan_mail_pro')
class TestExactlyOneNotificationMailbox(MailProTestCase):

    def test_ticking_one_unticks_the_other(self):
        self.shared_mailbox.write({
            'is_notification_mailbox': True, 'owner_user_id': self.notif_owner.id})
        self.assertTrue(self.shared_mailbox.is_notification_mailbox)
        self.assertFalse(self.notification_mailbox.is_notification_mailbox)
        self.assertEqual(self.env['pan.mail.mailbox'].search_count(
            [('is_notification_mailbox', '=', True)]), 1)

    def test_creating_one_unticks_the_other(self):
        created = self.env['pan.mail.mailbox'].create({
            'email': 'system@company.test',
            'is_notification_mailbox': True,
            'owner_user_id': self.notif_owner.id,
        })
        self.assertFalse(self.notification_mailbox.is_notification_mailbox)
        self.assertTrue(created.is_notification_mailbox)

    def test_unticking_the_last_one_is_refused(self):
        with self.assertRaisesRegex(UserError, 'Pick another mailbox for notifications first.'):
            self.notification_mailbox.is_notification_mailbox = False
        self.assertTrue(self.notification_mailbox.is_notification_mailbox)

    def test_unticking_an_unticked_one_is_fine(self):
        self.shared_mailbox.is_notification_mailbox = False
        self.assertTrue(self.notification_mailbox.is_notification_mailbox)

    def test_archiving_the_last_one_is_refused(self):
        with self.assertRaisesRegex(UserError, 'Pick another mailbox for notifications first.'):
            self.notification_mailbox.action_archive()
        self.assertTrue(self.notification_mailbox.active)

    def test_deleting_the_last_one_is_refused(self):
        with self.assertRaisesRegex(UserError, 'Pick another mailbox for notifications first.'):
            self.notification_mailbox.unlink()
        self.assertTrue(self.notification_mailbox.exists())

    def test_once_moved_the_old_one_may_go(self):
        self.shared_mailbox.write({
            'is_notification_mailbox': True, 'owner_user_id': self.notif_owner.id})
        self.notification_mailbox.action_archive()
        self.assertFalse(self.notification_mailbox.active)

    def test_every_mailbox_may_go_together(self):
        """Back to before setup: nothing left to pick."""
        self.env['pan.mail.mailbox'].with_context(active_test=False).search([]).unlink()
        self.assertFalse(self.env['pan.mail.mailbox'].with_context(
            active_test=False).search_count([]))
