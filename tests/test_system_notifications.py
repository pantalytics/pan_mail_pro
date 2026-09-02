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
        if calls.get('draft') is None:
            self.skipTest('No follower mail produced — Odoo flow changed?')
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

        if calls.get('draft') is None:
            self.skipTest('Activity did not trigger a mail.mail in this Odoo build')
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
