# -*- coding: utf-8 -*-
"""
Composer integration tests for res.partner chatter.

Two flows:
- Inline chatter "Send message" (no composer dialog) → goes via message_post
  directly with author=env.user; no x_microsoft_mailbox_id set, so routing
  uses the user's default mailbox.
- Composer dialog with explicit dropdown choice → dropdown wins.

Note: message_post triggers send_after_commit() immediately in test mode,
so all post operations must run inside mock_graph().
"""
from odoo.tests import tagged

from .common import OutlookProTestCase


@tagged('pan_outlook_pro', 'post_install', '-at_install')
class TestComposeResPartner(OutlookProTestCase):

    def test_inline_chatter_uses_user_default_mailbox(self):
        """No composer, no dropdown → routing falls through to author's
        default mailbox (the salesperson's shared_mailbox)."""
        with self.mock_graph() as calls:
            self.external_partner.with_user(self.salesperson).sudo().message_post(
                body='<p>Hello</p>',
                subject='Direct chatter',
                partner_ids=[self.external_partner.id],
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
            )
        self.assertTrue(calls.get('draft'),
                        "message_post should have triggered a Graph send")
        self.assertNotEqual(
            calls['draft']['from']['emailAddress']['address'],
            self.notification_mailbox.email,
            "Salesperson with default mailbox must not fall back to notifications@",
        )

    def test_composer_dialog_dropdown_wins(self):
        """Full composer with explicit personal_mailbox in the dropdown."""
        with self.mock_graph() as calls:
            composer = self.env['mail.compose.message'].with_user(self.salesperson).sudo().with_context({
                'default_model': 'res.partner',
                'default_res_ids': [self.external_partner.id],
                'default_composition_mode': 'comment',
            }).create({
                'subject': 'Hi',
                'body': '<p>Body</p>',
                'model': 'res.partner',
                'res_ids': str([self.external_partner.id]),
                'composition_mode': 'comment',
                'partner_ids': [(6, 0, [self.external_partner.id])],
                'x_microsoft_send_from_id': self.personal_mailbox.id,
            })
            composer.action_send_mail()
        self.assertEqual(
            calls['draft']['from']['emailAddress']['address'],
            self.personal_mailbox.email,
        )

    def test_reply_in_chatter_preserves_dropdown(self):
        """A reply on an existing thread should still honor the dropdown."""
        # Seed a parent message inside one mock_graph (just to drain it)
        with self.mock_graph():
            parent = self.external_partner.with_user(self.salesperson).sudo().message_post(
                body='<p>Initial</p>', subject='Thread start',
                message_type='comment', subtype_xmlid='mail.mt_comment',
            )

        with self.mock_graph() as calls:
            composer = self.env['mail.compose.message'].with_user(self.salesperson).sudo().with_context({
                'default_model': 'res.partner',
                'default_res_ids': [self.external_partner.id],
                'default_composition_mode': 'comment',
                'default_parent_id': parent.id,
            }).create({
                'subject': 'Re: Thread start',
                'body': '<p>Reply</p>',
                'model': 'res.partner',
                'res_ids': str([self.external_partner.id]),
                'composition_mode': 'comment',
                'parent_id': parent.id,
                'partner_ids': [(6, 0, [self.external_partner.id])],
                'x_microsoft_send_from_id': self.shared_mailbox.id,
            })
            composer.action_send_mail()
        self.assertEqual(
            calls['draft']['from']['emailAddress']['address'],
            self.shared_mailbox.email,
        )
