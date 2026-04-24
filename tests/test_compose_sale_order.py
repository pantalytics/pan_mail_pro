# -*- coding: utf-8 -*-
"""
Composer integration tests for sale.order quotation send.

Reproduces the production bug end-to-end: a quotation template with email_from
hardcoded to the company address (e.g. "Acme <info@company.test>") makes
Odoo's _message_compute_author resolve author_id to the company partner.
Without the fix this falls back to the notification mailbox; with the fix the
dropdown selection wins.
"""
from odoo.tests import tagged

from .common import OutlookProTestCase


@tagged('pan_outlook_pro', 'post_install', '-at_install')
class TestComposeSaleOrder(OutlookProTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if 'sale.order' not in cls.env:
            return  # skipTest enforced in setUp

        cls.product = cls._silent('product.product').create({
            'name': 'Test Product',
            'list_price': 100.0,
        })
        cls.sale_order = cls._silent('sale.order').create({
            'partner_id': cls.external_partner.id,
            'user_id': cls.salesperson.id,
            'order_line': [(0, 0, {
                'product_id': cls.product.id,
                'product_uom_qty': 1,
                'price_unit': 100.0,
            })],
        })

        # Template with email_from hardcoded to the company-matching address
        # — exactly the production bug scenario.
        cls.template = cls._silent('mail.template').create({
            'name': 'Test Quotation Template (bad)',
            'model_id': cls.env['ir.model']._get('sale.order').id,
            'subject': 'Quotation {{ object.name }}',
            'body_html': '<p>Please find quotation attached.</p>',
            'email_from': '"Acme" <info@company.test>',
            'partner_to': '{{ object.partner_id.id }}',
        })
        # Template with email_from based on the salesperson — the "good" pattern
        # used for happy-path regression checks.
        cls.template_user_based = cls._silent('mail.template').create({
            'name': 'Test Quotation Template (user-based)',
            'model_id': cls.env['ir.model']._get('sale.order').id,
            'subject': 'Quotation {{ object.name }}',
            'body_html': '<p>Please find quotation attached.</p>',
            'email_from': '{{ object.user_id.email_formatted }}',
            'partner_to': '{{ object.partner_id.id }}',
        })

    def setUp(self):
        super().setUp()
        if 'sale.order' not in self.env:
            self.skipTest('sale module not installed')

    def _send_via_composer(self, dropdown_mailbox=None, attachments=None,
                           template=None):
        """Open composer, optionally set dropdown, fire send. Returns mock calls."""
        template = template or self.template
        with self.mock_graph() as calls:
            composer = self.env['mail.compose.message'].with_user(self.salesperson).sudo().with_context({
                'default_model': 'sale.order',
                'default_res_ids': [self.sale_order.id],
                'default_template_id': template.id,
                'default_composition_mode': 'comment',
            }).create({
                'subject': 'Quotation ' + self.sale_order.name,
                'body': '<p>Please find quotation attached.</p>',
                'model': 'sale.order',
                'res_ids': str([self.sale_order.id]),
                'template_id': template.id,
                'composition_mode': 'comment',
                'partner_ids': [(6, 0, [self.external_partner.id])],
            })
            if dropdown_mailbox is not None:
                composer.x_microsoft_send_from_id = dropdown_mailbox.id
            else:
                composer.x_microsoft_send_from_id = False
            if attachments:
                composer.attachment_ids = [(6, 0, [a.id for a in attachments])]
            composer.action_send_mail()
        return calls

    # ------------------------------------------------------------------ #

    def test_dropdown_wins_when_template_email_from_matches_company(self):
        """Bug-of-the-day reproduction: dropdown selection wins even when
        author_id resolves to the company partner."""
        calls = self._send_via_composer(dropdown_mailbox=self.shared_mailbox)
        self.assertEqual(
            calls['draft']['from']['emailAddress']['address'],
            self.shared_mailbox.email,
            "Email must be sent from the dropdown-selected mailbox, not notifications@",
        )

    def test_no_dropdown_with_user_based_template_uses_salesperson_default(self):
        """Happy path: with a salesperson-based email_from, no dropdown needed —
        author resolves to the user and their default mailbox is used."""
        calls = self._send_via_composer(
            dropdown_mailbox=None,
            template=self.template_user_based,
        )
        self.assertNotEqual(
            calls['draft']['from']['emailAddress']['address'],
            self.notification_mailbox.email,
            "Salesperson with default mailbox must not fall back to notifications@",
        )

    def test_no_dropdown_with_bad_template_falls_back_to_notification(self):
        """Documented current behavior: when the template hardcodes the
        company email AND the user did not pick a dropdown, the routing
        legitimately falls back to notifications@. The fix ensures the
        dropdown wins when set; without one, this fallback is the safest
        option since author has no user."""
        calls = self._send_via_composer(dropdown_mailbox=None)
        self.assertEqual(
            calls['draft']['from']['emailAddress']['address'],
            self.notification_mailbox.email,
        )

    def test_explicit_dropdown_overrides_user_default(self):
        """User has default=shared_mailbox, dropdown=personal_mailbox → personal wins."""
        calls = self._send_via_composer(dropdown_mailbox=self.personal_mailbox)
        self.assertEqual(
            calls['draft']['from']['emailAddress']['address'],
            self.personal_mailbox.email,
        )

    def test_quotation_with_attachment(self):
        """Quotation send with an attached PDF must include it via direct upload."""
        attachment = self.make_attachment(name='Quote.pdf', size_bytes=2048)
        calls = self._send_via_composer(
            dropdown_mailbox=self.shared_mailbox,
            attachments=[attachment],
        )
        self.assertEqual(len(calls['attach']), 1)
        self.assertEqual(calls['attach'][0]['name'], 'Quote.pdf')
