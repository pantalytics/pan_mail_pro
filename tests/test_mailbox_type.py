# -*- coding: utf-8 -*-
"""The mailbox type is derived from the owner, never asked.

An owner on their own address makes a personal mailbox. No owner, or an owner
on a different address (whose token reads it, on Microsoft), makes a shared
one. The notification mailbox is personal whatever its address. A provider
cannot answer the question reliably, so the form does not ask it.
"""
from odoo.tests import tagged

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
            'email': 'Other@Test.local ',
            'owner_user_id': self.other_user.id,
        })
        self.assertEqual(by_grant.mailbox_type, 'personal')
        self.assertFalse(by_grant._is_sendable_by(self.salesperson))

        unconnected = self.env['res.users'].create({
            'name': 'Not Yet', 'login': 'notyet@test.local', 'email': 'notyet@test.local',
        })
        by_record = self.env['pan.mail.mailbox'].create({
            'email': 'notyet@test.local',
            'owner_user_id': unconnected.id,
        })
        self.assertEqual(by_record.mailbox_type, 'personal')

    def test_type_follows_the_owner_when_it_changes(self):
        mailbox = self.env['pan.mail.mailbox'].create({
            'email': 'support@company.test',
            'owner_user_id': self.salesperson.id,
        })
        self.assertEqual(mailbox.mailbox_type, 'shared')
        self.connect(self.salesperson, email='support@company.test')
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
        user = self.env['res.users'].create({
            'name': 'New Hire', 'login': 'hire@test.local', 'email': 'hire@test.local',
        })
        self.connect(user, email='hire@company.test')
        mailbox = self.env['pan.mail.mailbox'].create({
            'email': 'hire@company.test', 'provider': 'outlook',
            'owner_user_id': user.id,
        })
        self.assertEqual(mailbox.mailbox_type, 'personal')
