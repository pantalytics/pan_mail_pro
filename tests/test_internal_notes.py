# -*- coding: utf-8 -*-
"""
Internal note tests across apps.

Internal notes use subtype 'mail.mt_note' and must NOT trigger outbound mail.
The exception is when an @mention targets a user with notification_type='email':
that should produce a mail.mail routed via the notification mailbox (not via
any leaked dropdown).

All operations run inside mock_graph() because Odoo's send_after_commit() fires
immediately in test mode.
"""
from odoo.tests import tagged

from .common import MailProTestCase


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestInternalNotes(MailProTestCase):

    def _post_note(self, record, partner_ids=None):
        return record.with_user(self.salesperson).sudo().message_post(
            body='<p>Internal note</p>',
            subject='Note',
            message_type='comment',
            subtype_xmlid='mail.mt_note',
            partner_ids=partner_ids or [],
        )

    def test_internal_note_on_partner_creates_no_outbound_mail(self):
        with self.mock_graph() as calls:
            self._post_note(self.external_partner)
        self.assertIsNone(calls.get('draft'),
                          "internal notes must not trigger Graph send")

    def test_internal_note_with_inbox_user_mention_no_email(self):
        """@mention an inbox-only user: no Graph send (no mail.mail produced)."""
        with self.mock_graph() as calls:
            self._post_note(self.external_partner,
                            partner_ids=[self.inbox_user.partner_id.id])
        self.assertIsNone(calls.get('draft'))

    def test_internal_note_with_email_user_mention_uses_notification_mailbox(self):
        """@mention a user with notification_type='email': mail.mail is created
        and must route via the notification mailbox, not via any dropdown."""
        with self.mock_graph() as calls:
            self._post_note(self.external_partner,
                            partner_ids=[self.other_user.partner_id.id])
        self.assertIsNotNone(calls.get('draft'),
                             "@mention to email-user must trigger Graph send")
        self.assertEqual(
            calls['draft']['from']['emailAddress']['address'],
            self.notification_mailbox.email,
            "internal-user notifications must always go via notifications@",
        )

    def test_internal_note_on_crm_lead_no_outbound_mail(self):
        lead = self._silent('crm.lead').create({
            'name': 'L',
            'partner_id': self.external_partner.id,
            'user_id': self.salesperson.id,
        })
        with self.mock_graph() as calls:
            self._post_note(lead)
        self.assertIsNone(calls.get('draft'))

    def test_internal_note_on_sale_order_no_outbound_mail(self):
        if 'sale.order' not in self.env:
            self.skipTest('sale module not installed')
        product = self._silent('product.product').create({
            'name': 'P', 'list_price': 1.0,
        })
        order = self._silent('sale.order').create({
            'partner_id': self.external_partner.id,
            'user_id': self.salesperson.id,
            'order_line': [(0, 0, {
                'product_id': product.id,
                'product_uom_qty': 1, 'price_unit': 1.0,
            })],
        })
        with self.mock_graph() as calls:
            self._post_note(order)
        self.assertIsNone(calls.get('draft'))
