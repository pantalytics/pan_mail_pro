# -*- coding: utf-8 -*-
"""The user's Odoo signature reaches the provider.

Mail Pro sends what `mail.mail.body_html` holds, and that body is Odoo's
notification layout around the composer text: the layout is where the
signature (My Preferences → Signature) is pasted in, at send time, never
in the composer. Nothing in this module touches that, which is exactly why
it needs a test: a layout override, a `body` instead of `body_html` in a
provider, or a composer opened with the wrong context would each drop the
signature without a single failing line elsewhere.
"""
from odoo.tests import tagged

from .common import MailProTestCase


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestComposeSignature(MailProTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.salesperson.signature = '<p>Sales Person<br/>Acme Sales Team</p>'
        cls.lead = cls._silent('crm.lead').create({
            'name': 'Signature Lead',
            'partner_id': cls.external_partner.id,
            'user_id': cls.salesperson.id,
            'email_from': cls.external_partner.email,
        })
        cls.template = cls._silent('mail.template').create({
            'name': 'CRM With Own Sign-off',
            'model_id': cls.env['ir.model']._get('crm.lead').id,
            'subject': 'Re: {{ object.name }}',
            'body_html': '<p>From a template.</p>',
            'email_from': '{{ object.user_id.email_formatted }}',
            'partner_to': '{{ object.partner_id.id }}',
        })

    def _send_via_composer(self, template=None):
        """The Inbox's reply: comment mode on the record, the salesperson
        writing, the personal mailbox chosen in Send From."""
        context = {
            'default_model': 'crm.lead',
            'default_res_ids': [self.lead.id],
            'default_composition_mode': 'comment',
        }
        vals = {
            'subject': 'Re: Signature Lead',
            'body': '<p>Typed in the composer.</p>',
            'model': 'crm.lead',
            'res_ids': str([self.lead.id]),
            'composition_mode': 'comment',
            'partner_ids': [(6, 0, [self.external_partner.id])],
        }
        if template is not None:
            # The template fills subject and body; a body handed to create()
            # would win over it and test nothing about templates.
            context['default_template_id'] = template.id
            vals['template_id'] = template.id
            del vals['subject'], vals['body']
        with self.mock_graph() as calls:
            composer = self.env['mail.compose.message'].with_user(
                self.salesperson).sudo().with_context(**context).create(vals)
            composer.x_send_from_mailbox_id = self.personal_mailbox.id
            composer.action_send_mail()
        return calls['draft']['body']['content']

    def test_a_plain_reply_carries_the_author_s_odoo_signature(self):
        body = self._send_via_composer()
        self.assertIn('Typed in the composer.', body)
        self.assertIn('Acme Sales Team', body)

    def test_a_template_reply_carries_no_signature(self):
        """A template brings its own sign-off; Odoo adds none on top, and
        neither do we. Pinned so a change in either direction is a
        decision rather than a surprise."""
        body = self._send_via_composer(self.template)
        self.assertIn('From a template.', body)
        self.assertNotIn('Acme Sales Team', body)
