# -*- coding: utf-8 -*-
"""The "Send Test Email" button.

The two tests that already existed both read: one asks the provider whether the
credentials still work, the other fetches a message. Sending was the untested
half, and it is the half that fails silently — a missing send scope, a SendAs
that was never granted, a DMARC record that rejects the address.

What is asserted here is mostly about *who the test mail goes to*. It goes to
the person who pressed the button and never to the mailbox itself, because a
mail addressed to the mailbox comes straight back in through the sync.
"""
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import MailProTestCase

SUBJECT = 'Mail Pro test email'


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestMailboxTestSend(MailProTestCase):

    def _mails(self):
        return self.env['mail.mail'].search([('subject', '=', SUBJECT)])

    # ------------------------------------------------------------------ #
    # The happy path
    # ------------------------------------------------------------------ #

    def test_sends_to_the_user_who_pressed_it(self):
        """Not to the mailbox: that mail would be re-imported by the sync."""
        mailbox = self.shared_mailbox.with_user(self.salesperson)
        with self.mock_graph() as calls:
            result = mailbox.action_test_send()

        recipients = [
            r['emailAddress']['address']
            for r in calls['draft']['toRecipients']
        ]
        self.assertEqual(recipients, [self.salesperson.email])
        self.assertNotIn(self.shared_mailbox.email, recipients)
        self.assertEqual(result['params']['type'], 'success')

    def test_sends_from_the_mailbox_it_was_pressed_on(self):
        """The point of the test is this mailbox, so routing may not reroute."""
        mailbox = self.notification_mailbox.with_user(self.notif_owner)
        with self.mock_graph():
            mailbox.action_test_send()

        mail = self._mails()
        self.assertEqual(mail.x_send_from_mailbox_id, self.notification_mailbox)
        self.assertEqual(mail.state, 'sent')

    # ------------------------------------------------------------------ #
    # Refusals
    # ------------------------------------------------------------------ #

    def test_user_without_an_email_address_is_told_why(self):
        self.other_user.email = False
        with self.assertRaises(UserError):
            self.shared_mailbox.with_user(self.other_user).action_test_send()

    def test_failure_leaves_nothing_in_the_queue(self):
        """A failed test must not be delivered by the cron minutes later."""
        self.disconnect(self.salesperson)
        result = self.shared_mailbox.with_user(self.salesperson).action_test_send()

        self.assertEqual(result['params']['type'], 'danger')
        self.assertFalse(self._mails())

    def test_somebody_elses_personal_mailbox_is_refused(self):
        """A refusal is raised, not reported as a red toast: it is the one
        outcome the reader can do something about."""
        with self.assertRaises(UserError):
            self.personal_mailbox.with_user(self.other_user).action_test_send()

    # ------------------------------------------------------------------ #
    # The same button on My Profile
    # ------------------------------------------------------------------ #

    def test_profile_button_uses_the_users_own_sending_address(self):
        with self.mock_graph() as calls:
            self.salesperson.with_user(
                self.salesperson).action_test_send_mailbox()

        self.assertEqual(
            calls['draft']['from']['emailAddress']['address'],
            self.shared_mailbox.email,
        )

    def test_profile_button_refuses_another_users_mailbox(self):
        with self.assertRaises(UserError):
            self.other_user.with_user(
                self.salesperson).action_test_send_mailbox()

    def test_profile_button_asks_for_a_sending_address_first(self):
        self.assertFalse(self.other_user.x_default_mailbox_id)
        with self.assertRaises(UserError):
            self.other_user.with_user(
                self.other_user).action_test_send_mailbox()
