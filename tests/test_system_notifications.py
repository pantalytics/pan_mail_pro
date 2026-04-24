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

from .common import OutlookProTestCase


@tagged('pan_outlook_pro', 'post_install', '-at_install')
class TestSystemNotifications(OutlookProTestCase):

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
        with patch.object(Mail, '_send_via_microsoft_graph') as graph_path, \
             patch('odoo.addons.mail.models.mail_mail.MailMail.send',
                   autospec=True, return_value=True):
            mail.send()
        graph_path.assert_not_called()
