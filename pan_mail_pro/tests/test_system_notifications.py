# -*- coding: utf-8 -*-
"""
System-triggered email tests (no composer):
- Activity assignments
- Follower notifications
- Mass mailing routing

All operations run inside mock_graph() because Odoo's send_after_commit()
fires immediately in test mode.
"""
from odoo.tests import tagged

from .common import MailProTestCase


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestSystemNotifications(MailProTestCase):

    def test_follower_notification_routes_via_notification_mailbox(self):
        """When a record posts a tracked message and a follower with
        notification_type='email' exists, the follower mail.mail must
        route via the notification mailbox."""
        self.external_partner.sudo().message_subscribe(
            partner_ids=[self.other_user.partner_id.id])

        with self.mock_graph() as calls:
            self.external_partner.with_user(self.salesperson).sudo().message_post(
                body='<p>Triggers notification</p>',
                subject='Update',
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
            )
        # The follower's mail (other_user) must route via notification mailbox
        self.assertIsNotNone(calls.get('draft'), 'No follower mail produced')
        self.assertEqual(
            calls['draft']['from']['emailAddress']['address'],
            self.notification_mailbox.email,
        )

    def test_activity_assignment_uses_notification_mailbox(self):
        """Assigning an activity to another user → email notification via
        notification mailbox (not via the assigner's default)."""
        with self.mock_graph() as calls:
            self.env['mail.activity'].with_user(self.salesperson).sudo().create({
                'res_model_id': self.env['ir.model']._get('res.partner').id,
                'res_id': self.external_partner.id,
                'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
                'user_id': self.other_user.id,
                'summary': 'Please follow up',
            })

        self.assertIsNotNone(calls.get('draft'), 'Activity assignment produced no mail')
        self.assertEqual(
            calls['draft']['from']['emailAddress']['address'],
            self.notification_mailbox.email,
        )

    def test_recipientless_notification_does_not_abort_batch(self):
        """A notification to an internal user with no email address must be
        cancelled individually — it must NOT raise and abort the whole batch,
        which would also block the real, deliverable email sent alongside it.

        Reproduces the production case where the Administrator account had no
        email but notification_type='email', so every composer send failed with
        'No recipients specified'."""
        # Internal user whose partner has no email address.
        no_email_user = self._silent('res.users').create({
            'name': 'No Email User',
            'login': 'no_email@test.local',
            'notification_type': 'email',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        self.assertFalse(no_email_user.partner_id.email)

        # The real, deliverable email the user actually composed.
        deliverable = self.env['mail.mail'].sudo().create({
            'subject': 'Real message',
            'body_html': '<p>hi</p>',
            'author_id': self.salesperson.partner_id.id,
            'recipient_ids': [(6, 0, [self.external_partner.id])],
        })
        # The undeliverable internal-user notification created in the same flow.
        notification = self.env['mail.mail'].sudo().create({
            'subject': 'Notification copy',
            'body_html': '<p>hi</p>',
            'author_id': self.salesperson.partner_id.id,
            'recipient_ids': [(6, 0, [no_email_user.partner_id.id])],
        })

        with self.mock_graph():
            # Must not raise even though `notification` has no deliverable address.
            (deliverable | notification).send()

        self.assertEqual(
            deliverable.state, 'sent',
            "The deliverable email must still send when a recipient-less "
            "notification is in the same batch")
        self.assertEqual(
            notification.state, 'cancel',
            "A recipient-less internal notification must be cancelled, not raised")

    def test_cancelling_a_mail_cancels_its_notifications(self):
        """A cancelled mail must not leave notification rows claiming it is
        still queued.

        `mail.mail` is garbage-collected and `mail.notification` is not, so the
        row that survives is the one anybody reading the database later sees.
        At one customer, seventeen of them read `ready` eleven days after the
        mails were cancelled.
        """
        no_email_user = self._silent('res.users').create({
            'name': 'Also No Email',
            'login': 'also_no_email@test.local',
            'notification_type': 'email',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        message = self.external_partner.sudo().message_post(
            body='<p>Something happened</p>',
            message_type='notification',
            subtype_xmlid='mail.mt_note',
        )
        mail = self.env['mail.mail'].sudo().create({
            'subject': 'Notification copy',
            'body_html': '<p>hi</p>',
            'author_id': self.salesperson.partner_id.id,
            'mail_message_id': message.id,
            'is_notification': True,
            'recipient_ids': [(6, 0, [no_email_user.partner_id.id])],
        })
        notification = self.env['mail.notification'].sudo().create({
            'mail_mail_id': mail.id,
            'mail_message_id': message.id,
            'res_partner_id': no_email_user.partner_id.id,
            'notification_type': 'email',
            'notification_status': 'ready',
        })

        with self.mock_graph():
            mail.send()

        self.assertEqual(mail.state, 'cancel')
        self.assertEqual(
            notification.notification_status, 'canceled',
            "A cancelled mail's notification must not stay at 'ready', which "
            "the chatter renders as still pending")

    def test_mass_mailing_bypasses_graph(self):
        """Mass mailing emails (with mailing_id) must NOT go through Graph API."""
        if 'mailing_id' not in self.env['mail.mail']._fields:
            self.skipTest('mass_mailing module not installed')

        from unittest.mock import patch
        mailing = self.env['mailing.mailing'].create({
            'subject': 'Campaign',
            'body_html': '<p>Hello</p>',
            'mailing_model_id': self.env['ir.model']._get('res.partner').id,
        })
        mail = self.env['mail.mail'].sudo().create({
            'subject': 'Campaign mail',
            'body_html': '<p>Hello</p>',
            'email_to': 'customer@example.com',
            'author_id': self.salesperson.partner_id.id,
            'mailing_id': mailing.id,
        })

        Mail = type(self.env['mail.mail'])
        with patch.object(Mail, '_send_one') as graph_path, \
             patch('odoo.addons.mail.models.mail_mail.MailMail.send',
                   autospec=True, return_value=True):
            mail.send()
        graph_path.assert_not_called()

    def test_password_reset_uses_notification_mailbox(self):
        """A password reset must not need a mailbox on anybody's profile.

        Reproduces the production shape exactly (auth_signup
        `_action_reset_password`): `message_type='user_notification'`,
        `recipient_ids` empty, the recipient named in `email_to`, and
        `email_from` the company address — from which `mail.message` resolves an
        author who never chose to send anything. That author used to decide the
        route, so a database whose admin has no default mailbox refused to send
        its own password resets and nobody could log back in.
        """
        self.assertFalse(
            self.other_user.x_default_mailbox_id,
            "Fixture must have no default mailbox for this test to mean anything")

        mail = self.env['mail.mail'].sudo().create({
            'subject': 'Password reset',
            'body_html': '<p>Reset your password</p>',
            'message_type': 'user_notification',
            'email_from': self.company_partner.email_formatted,
            'author_id': self.other_user.partner_id.id,
            'email_to': self.other_user.email,
            'recipient_ids': [],
        })

        with self.mock_graph() as calls:
            mail.send()

        self.assertEqual(mail.state, 'sent', mail.failure_reason)
        self.assertEqual(
            calls['draft']['from']['emailAddress']['address'],
            self.notification_mailbox.email,
        )

    def test_portal_invite_uses_notification_mailbox(self):
        """Account mail to a customer is account mail too.

        The portal invitation takes the same path as the reset above, and its
        recipient is a `share=True` user — so the internal-employee test says
        no. It is still the database handing out a login, not a salesperson
        writing to a customer.
        """
        mail = self.env['mail.mail'].sudo().create({
            'subject': 'Your account',
            'body_html': '<p>Set your password</p>',
            'message_type': 'user_notification',
            'email_from': self.company_partner.email_formatted,
            'author_id': self.other_user.partner_id.id,
            'email_to': self.portal_user.email,
            'recipient_ids': [],
        })

        with self.mock_graph() as calls:
            mail.send()

        self.assertEqual(mail.state, 'sent', mail.failure_reason)
        self.assertEqual(
            calls['draft']['from']['emailAddress']['address'],
            self.notification_mailbox.email,
        )
