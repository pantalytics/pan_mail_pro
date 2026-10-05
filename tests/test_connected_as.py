# -*- coding: utf-8 -*-
"""Which identity a user is connected as, and what it may send from.

The account row always stored the address the provider reported at consent.
Nothing showed it, so a person who signs in to Odoo as a colleague and
consents with their own Microsoft identity was invisible on every screen:
"connected", a tick, and a shared mailbox that refused to send for a reason
that named the wrong person. The refusal itself is worded in the Microsoft
client (`_delegation_denied_reason`); this file is the screens.
"""
from odoo.tests import tagged

from .common import MailProTestCase


@tagged('post_install', '-at_install', 'pan_mail_pro')
class TestConnectedAs(MailProTestCase):

    def test_connected_as_is_the_address_the_provider_reported(self):
        """The fixture salesperson consented as sales@company.test on a user
        record that says sales@test.local, which is the customer case."""
        self.assertEqual(self.salesperson.x_pan_mail_connected_as, 'sales@company.test')
        self.assertTrue(self.salesperson.x_pan_mail_connected_elsewhere)

    def test_own_address_is_not_elsewhere(self):
        self.assertEqual(self.other_user.x_pan_mail_connected_as, 'other@test.local')
        self.assertFalse(self.other_user.x_pan_mail_connected_elsewhere)

    def test_disconnecting_clears_the_address(self):
        self.disconnect(self.other_user)
        self.assertFalse(self.other_user.x_pan_mail_connected_as)
        self.assertFalse(self.other_user.x_pan_mail_connected_elsewhere)

    def test_the_user_may_read_their_own(self):
        """Self-readable, or My Preferences cannot show it."""
        me = self.salesperson.with_user(self.salesperson)
        self.assertEqual(me.read(['x_pan_mail_connected_as'])[0]['x_pan_mail_connected_as'],
                         'sales@company.test')
        self.assertTrue(me.read(['x_pan_mail_connected_elsewhere'])[0]['x_pan_mail_connected_elsewhere'])

    def test_the_settings_page_counts_who_is_connected_elsewhere(self):
        """One line, only when it happens."""
        self.env['pan.mail.provider'].create({
            'provider': 'outlook', 'client_id': 'id', 'client_secret': 'secret',
            'tenant_id': 'common',
        })
        Settings = self.env['res.config.settings']
        self.assertIn('1 connected as another address', Settings.create({}).x_users_elsewhere_note)

        self.salesperson.sudo().x_pan_mail_account_ids.write({'email': 'sales@test.local'})
        self.assertFalse(Settings.create({}).x_users_elsewhere_note)


@tagged('post_install', '-at_install', 'pan_mail_pro')
class TestSendsWith(MailProTestCase):
    """`_resolve_sending_account` in one sentence on the mailbox form."""

    def test_a_personal_mailbox_names_its_owner_and_their_sign_in(self):
        self.assertEqual(
            self.personal_mailbox.sends_with,
            "Sends with Sales Person's sign-in, sales@company.test")

    def test_the_notification_mailbox_is_the_owners_too(self):
        self.assertEqual(
            self.notification_mailbox.sends_with,
            "Sends with Notification Owner's sign-in, notif_owner@test.local")

    def test_an_unconnected_owner_is_said_so(self):
        self.disconnect(self.notif_owner)
        self.assertEqual(
            self.notification_mailbox.sends_with,
            "Sends with Notification Owner's sign-in, not connected yet")

    def test_a_microsoft_shared_mailbox_sends_as_whoever_writes(self):
        self.assertEqual(self.shared_mailbox.sends_with, "Sends with each sender's own sign-in")

    def test_a_gmail_shared_mailbox_is_its_own_account(self):
        mailbox = self.env['pan.mail.mailbox'].create({
            'email': 'team@company.test', 'provider': 'gmail',
        })
        self.assertEqual(mailbox.sends_with, 'Sends with its own account')
