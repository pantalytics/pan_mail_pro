# -*- coding: utf-8 -*-
"""The communication lens: fields, click-through, and the searchable compute.

Three things are worth pinning here, in descending order of how quietly they
break:

1. `_search_x_delivery_state` must return the same rows the compute would. A
   compute with a wrong search does not error — it silently returns the wrong
   set, and in a screen that reports "delivery failed" that reads as "nothing
   failed". This is the classic bug of the pattern.

2. `x_document_name` must not leak names past the document's own ACL, and must
   survive an uninstalled model and a deleted record without raising, because
   one bad row would otherwise blank the entire list.

3. Both stamping points must actually fire. They ride along on writes that
   already happen, which is efficient but also easy to lose in a refactor.
"""
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import OutlookProTestCase


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestMailLens(OutlookProTestCase):

    # ------------------------------------------------------------------ #
    # Stamping
    # ------------------------------------------------------------------ #
    def test_outgoing_mail_is_stamped(self):
        mail = self.env['mail.mail'].sudo().create({
            'subject': 'Quote',
            'body_html': '<p>Attached</p>',
            'email_to': 'customer@example.com',
            'author_id': self.salesperson.partner_id.id,
            'x_microsoft_mailbox_id': self.shared_mailbox.id,
            'model': 'res.partner',
            'res_id': self.external_partner.id,
        })
        with self.mock_graph():
            mail.send()

        message = mail.mail_message_id
        self.assertEqual(message.x_direction, 'outgoing')
        self.assertEqual(message.x_mailbox_id, self.shared_mailbox)
        self.assertTrue(message.x_account_id, "the credentials used must be recorded")

    def test_res_model_id_follows_the_model_char(self):
        """The stored relation must track Odoo's own pointer."""
        message = self.env['mail.message'].sudo().create({
            'model': 'res.partner',
            'res_id': self.external_partner.id,
            'message_type': 'email',
            'subject': 'Hello',
        })
        self.assertEqual(message.x_res_model_id.model, 'res.partner')

        message.model = False
        self.assertFalse(message.x_res_model_id)

    # ------------------------------------------------------------------ #
    # Click-through
    # ------------------------------------------------------------------ #
    def _message_on_partner(self):
        return self.env['mail.message'].sudo().create({
            'model': 'res.partner',
            'res_id': self.external_partner.id,
            'message_type': 'email',
            'subject': 'Hello',
            'x_direction': 'incoming',
        })

    def test_document_name_resolves(self):
        message = self._message_on_partner()
        self.assertEqual(message.x_document_name, self.external_partner.display_name)

    def test_document_name_survives_a_deleted_record(self):
        message = self._message_on_partner()
        message.res_id = 999999999
        message.invalidate_recordset(['x_document_name'])
        self.assertFalse(message.x_document_name)

    def test_document_name_survives_an_uninstalled_model(self):
        message = self._message_on_partner()
        message.model = 'no.such.model'
        message.invalidate_recordset(['x_document_name'])
        self.assertFalse(message.x_document_name)

    def test_open_document_returns_the_record(self):
        action = self._message_on_partner().action_open_document()
        self.assertEqual(action['res_model'], 'res.partner')
        self.assertEqual(action['res_id'], self.external_partner.id)

    def test_open_document_refuses_an_unlinked_message(self):
        message = self.env['mail.message'].sudo().create({
            'message_type': 'email',
            'subject': 'Orphan',
            'x_direction': 'incoming',
        })
        with self.assertRaises(UserError):
            message.action_open_document()

    # ------------------------------------------------------------------ #
    # The searchable compute
    # ------------------------------------------------------------------ #
    def _message_with_notification(self, status):
        message = self._message_on_partner()
        self.env['mail.notification'].sudo().create({
            'mail_message_id': message.id,
            'res_partner_id': self.external_partner.id,
            'notification_type': 'email',
            'notification_status': status,
        })
        message.invalidate_recordset(['x_delivery_state'])
        return message

    def test_delivery_state_reflects_notifications(self):
        self.assertEqual(self._message_with_notification('sent').x_delivery_state, 'sent')
        self.assertEqual(self._message_with_notification('ready').x_delivery_state, 'pending')
        self.assertEqual(self._message_with_notification('exception').x_delivery_state, 'failed')
        self.assertEqual(self._message_with_notification('bounce').x_delivery_state, 'failed')

    def test_delivery_state_is_empty_without_notifications(self):
        self.assertFalse(self._message_on_partner().x_delivery_state)

    def test_search_matches_the_compute(self):
        """The whole point of the search override, asserted directly.

        Build one message per status, then check that searching for a state
        returns exactly the messages whose computed state is that value. A
        mismatch here is the silent failure this override exists to prevent.
        """
        messages = self.env['mail.message'].sudo()
        for status in ('sent', 'ready', 'exception', 'bounce'):
            messages |= self._message_with_notification(status)

        for state in ('sent', 'pending', 'failed'):
            expected = messages.filtered(lambda m, s=state: m.x_delivery_state == s)
            found = self.env['mail.message'].sudo().search([
                ('id', 'in', messages.ids),
                ('x_delivery_state', '=', state),
            ])
            self.assertEqual(
                set(found.ids), set(expected.ids),
                f"search and compute disagree for delivery state {state!r}",
            )

    def test_search_negation_matches_the_compute(self):
        messages = self.env['mail.message'].sudo()
        for status in ('sent', 'exception'):
            messages |= self._message_with_notification(status)

        found = self.env['mail.message'].sudo().search([
            ('id', 'in', messages.ids),
            ('x_delivery_state', '!=', 'failed'),
        ])
        expected = messages.filtered(lambda m: m.x_delivery_state != 'failed')
        self.assertEqual(set(found.ids), set(expected.ids))

