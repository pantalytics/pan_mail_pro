# -*- coding: utf-8 -*-
"""
Composer integration tests for crm.lead email send.

CRM uses {{ object.user_id.email_formatted }} in its templates by default,
which resolves author_id to the salesperson user — the "happy path". These
tests verify that:
1. The happy path still works (regression guard).
2. Dropdown override still wins.
3. A custom template that hardcodes email_from to the company partner
   triggers the same bug we fixed for sale.order, and is now handled.
"""
from odoo.tests import tagged

from .common import OutlookProTestCase


@tagged('pan_outlook_pro', 'post_install', '-at_install')
class TestComposeCrmLead(OutlookProTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.lead = cls._silent('crm.lead').create({
            'name': 'Test Lead',
            'partner_id': cls.external_partner.id,
            'user_id': cls.salesperson.id,
            'email_from': cls.external_partner.email,
        })
        cls.standard_template = cls._silent('mail.template').create({
            'name': 'CRM Standard',
            'model_id': cls.env['ir.model']._get('crm.lead').id,
            'subject': 'Re: {{ object.name }}',
            'body_html': '<p>Hi from CRM.</p>',
            'email_from': '{{ object.user_id.email_formatted }}',
            'partner_to': '{{ object.partner_id.id }}',
        })
        cls.bad_template = cls._silent('mail.template').create({
            'name': 'CRM Bad',
            'model_id': cls.env['ir.model']._get('crm.lead').id,
            'subject': 'Re: {{ object.name }}',
            'body_html': '<p>From company address.</p>',
            'email_from': '"Acme" <info@company.test>',
            'partner_to': '{{ object.partner_id.id }}',
        })

    def _send_via_composer(self, template, dropdown_mailbox=None):
        with self.mock_graph() as calls:
            composer = self.env['mail.compose.message'].with_user(self.salesperson).sudo().with_context({
                'default_model': 'crm.lead',
                'default_res_ids': [self.lead.id],
                'default_template_id': template.id,
                'default_composition_mode': 'comment',
            }).create({
                'subject': 'Re: Test Lead',
                'body': '<p>Body</p>',
                'model': 'crm.lead',
                'res_ids': str([self.lead.id]),
                'template_id': template.id,
                'composition_mode': 'comment',
                'partner_ids': [(6, 0, [self.external_partner.id])],
            })
            if dropdown_mailbox is not None:
                composer.x_microsoft_send_from_id = dropdown_mailbox.id
            else:
                composer.x_microsoft_send_from_id = False
            composer.action_send_mail()
        return calls

    # ------------------------------------------------------------------ #

    def test_standard_template_routes_via_salesperson_default(self):
        """Happy path: standard CRM template with user-based email_from."""
        calls = self._send_via_composer(self.standard_template)
        self.assertNotEqual(
            calls['draft']['from']['emailAddress']['address'],
            self.notification_mailbox.email,
        )

    def test_dropdown_override_wins_on_standard_template(self):
        calls = self._send_via_composer(self.standard_template,
                                        dropdown_mailbox=self.personal_mailbox)
        self.assertEqual(
            calls['draft']['from']['emailAddress']['address'],
            self.personal_mailbox.email,
        )

    def test_bad_template_with_dropdown_still_works(self):
        """Even if a CRM template has a hardcoded company email_from,
        an explicit dropdown choice must still be honored."""
        calls = self._send_via_composer(self.bad_template,
                                        dropdown_mailbox=self.shared_mailbox)
        self.assertEqual(
            calls['draft']['from']['emailAddress']['address'],
            self.shared_mailbox.email,
            "Dropdown must override company-partner author resolution in CRM too",
        )

    def test_lead_without_salesperson_dropdown_still_wins(self):
        """Lead without user_id but with explicit dropdown → dropdown wins."""
        self.lead.sudo().user_id = False
        calls = self._send_via_composer(self.standard_template,
                                        dropdown_mailbox=self.shared_mailbox)
        self.assertEqual(
            calls['draft']['from']['emailAddress']['address'],
            self.shared_mailbox.email,
        )
