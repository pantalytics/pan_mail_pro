# -*- coding: utf-8 -*-
"""Which identity a user is connected as, and what it may send from.

The account row always stored the address the provider reported at consent.
Nothing showed it, so a person who signs in to Odoo as a colleague and
consents with their own Microsoft identity was invisible on every screen:
"connected", a tick, and a shared mailbox that refused to send for a reason
that named the wrong person. The refusal itself is worded in the Microsoft
client (`_delegation_denied_reason`); this file is the screens.
"""
from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import MailProTestCase
from .common import groups_field


@tagged('post_install', '-at_install', 'pan_mail_pro')
class TestConnectedAs(MailProTestCase):

    def test_connected_as_is_the_address_the_provider_reported(self):
        """The fixture salesperson consented as sales@company.test on a user
        record that says sales@test.local, which is the customer case."""
        self.assertEqual(self.salesperson.x_pan_mail_connected_as, 'sales@company.test')

    def test_own_address_is_the_address(self):
        self.assertEqual(self.other_user.x_pan_mail_connected_as, 'other@test.local')

    def test_disconnecting_clears_the_address(self):
        self.disconnect(self.other_user)
        self.assertFalse(self.other_user.x_pan_mail_connected_as)

    def test_the_user_may_read_their_own(self):
        """Self-readable, or My Preferences cannot show it."""
        me = self.salesperson.with_user(self.salesperson)
        self.assertEqual(me.read(['x_pan_mail_connected_as'])[0]['x_pan_mail_connected_as'],
                         'sales@company.test')

    @mute_logger('odoo.addons.pan_mail_pro.models.pan_mail_account')
    def test_the_address_is_the_providers_word_and_not_a_managers(self):
        """The account's `email` is what "connected as" shows, and it is
        what `_compute_mailbox_type` reads to call a mailbox personal. A
        mailbox manager may read the row; rewriting the address is refused
        (`pan.mail.account.write`), so what the screen says somebody is
        connected as stays what the provider said at consent."""
        manager = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Manager', 'login': 'manager@company.test',
            'email': 'manager@company.test',
            groups_field(self.env): [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('pan_mail_pro.group_mail_mailbox_manager').id])],
        })
        account = self.salesperson.x_pan_mail_account_ids
        with self.assertRaises(AccessError):
            account.with_user(manager).write({'email': 'manager@company.test'})
        self.assertEqual(self.salesperson.x_pan_mail_connected_as, 'sales@company.test')

