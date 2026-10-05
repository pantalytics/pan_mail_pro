# -*- coding: utf-8 -*-
"""Which identity a user is connected as, and what it may send from.

The account row always stored the address the provider reported at consent.
Nothing showed it, so a person who signs in to Odoo as a colleague and
consents with their own Microsoft identity was invisible on every screen:
"connected", a tick, and a shared mailbox that refused to send for a reason
that named the wrong person.
"""
from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests import tagged

from odoo.addons.pan_mail_pro.models.mail_provider_client import get_provider_client

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

    def test_the_check_is_offered_where_a_sign_in_can_reach_a_shared_mailbox(self):
        """Microsoft sends from a shared mailbox with the person's own token;
        a Gmail shared address is its own account and there is nothing to
        check with somebody's sign-in."""
        self.assertTrue(self.other_user.x_pan_mail_can_check_access)
        gmail_user = self._silent('res.users').create({
            'name': 'Gmail User', 'login': 'gmail@test.local', 'email': 'gmail@test.local',
        })
        self.connect(gmail_user, provider='gmail')
        self.assertFalse(gmail_user.x_pan_mail_can_check_access)

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
class TestCheckMailboxAccess(MailProTestCase):
    """The button asks the provider, with the stored token, and stores nothing."""

    def _answering(self, reachable):
        """Patch the Microsoft probe to answer from a set of addresses, and
        record which (account, mailbox) pairs it was asked about."""
        asked = []

        def fake(client_self, account, mailbox):
            asked.append((account.email, mailbox.email))
            return mailbox.email in reachable

        Client = type(get_provider_client(self.env, 'outlook'))
        return patch.object(Client, 'check_mailbox_access', fake), asked

    def test_lists_what_the_sign_in_can_and_cannot_reach(self):
        patcher, asked = self._answering({'info@company.test'})
        with patcher:
            result = self.other_user.with_user(self.other_user).action_check_mailbox_access()

        message = result['params']['message']
        self.assertIn('other@test.local can send from info@company.test', message)
        self.assertNotIn('No access', message)
        self.assertEqual(result['params']['type'], 'success')
        # The shared mailbox only: the notification mailbox belongs to
        # somebody else, and a personal mailbox is the sign-in's own address.
        self.assertEqual(asked, [('other@test.local', 'info@company.test')])

    def test_names_the_identity_exchange_knows_not_the_odoo_user(self):
        """The salesperson's Odoo user says sales@test.local; the token is
        sales@company.test's, and that is who needs the delegation."""
        patcher, _asked = self._answering(set())
        with patcher:
            result = self.salesperson.with_user(self.salesperson).action_check_mailbox_access()

        message = result['params']['message']
        self.assertIn('No access to info@company.test', message)
        self.assertIn('Full Access and Send As', message)
        self.assertNotIn('sales@test.local', message)
        self.assertEqual(result['params']['type'], 'warning')
        self.assertTrue(result['params']['sticky'])

    def test_the_notification_mailbox_is_checked_for_its_owner(self):
        """It sends with its owner's token, like a personal one, so the
        owner's sign-in is the one that needs rights on it."""
        patcher, asked = self._answering({'notifications@company.test', 'info@company.test'})
        with patcher:
            self.notif_owner.with_user(self.notif_owner).action_check_mailbox_access()
        self.assertEqual(
            sorted(mailbox for _account, mailbox in asked),
            ['info@company.test', 'notifications@company.test'])

    def test_a_colleague_may_not_press_it_on_somebody_else(self):
        patcher, _asked = self._answering(set())
        with patcher, self.assertRaises(AccessError):
            self.salesperson.with_user(self.other_user).action_check_mailbox_access()

    def test_an_administrator_checks_a_colleague_with_their_stored_token(self):
        """Support: the admin checks Danielle's rights from Anna's user form
        without Danielle, and the token used is the one on that user."""
        patcher, asked = self._answering({'info@company.test'})
        admin = self.env.ref('base.user_admin')
        with patcher:
            result = self.salesperson.with_user(admin).action_check_mailbox_access()
        self.assertEqual(asked, [('sales@company.test', 'info@company.test')])
        self.assertIn('sales@company.test can send from', result['params']['message'])

    def test_nothing_to_check_says_so(self):
        self.shared_mailbox.active = False
        patcher, asked = self._answering(set())
        with patcher:
            result = self.other_user.with_user(self.other_user).action_check_mailbox_access()
        self.assertEqual(asked, [])
        self.assertIn('no shared mailbox', result['params']['message'])


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
