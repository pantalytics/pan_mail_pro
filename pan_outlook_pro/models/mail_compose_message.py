# -*- coding: utf-8 -*-
from odoo import fields, models, api


class MailComposeMessage(models.TransientModel):
    """Extend mail composer to add Microsoft mailbox selection"""
    _inherit = 'mail.compose.message'

    x_microsoft_send_from_id = fields.Many2one(
        'x_microsoft.mailbox',
        string='Send From',
        domain=[('active', '=', True)],
        help='Select which Microsoft mailbox to send this email from'
    )

    @api.model
    def default_get(self, fields_list):
        """Set default mailbox from user preferences"""
        result = super().default_get(fields_list)

        if 'x_microsoft_send_from_id' in fields_list:
            user = self.env.user
            # Use user's default mailbox if set and still active
            if user.x_microsoft_default_mailbox_id and user.x_microsoft_default_mailbox_id.active:
                result['x_microsoft_send_from_id'] = user.x_microsoft_default_mailbox_id.id

        return result

    def _action_send_mail_comment(self, res_ids):
        """Post chatter message, then update related mail.mail records."""
        messages = super()._action_send_mail_comment(res_ids)

        if self.x_microsoft_send_from_id and messages:
            mails = self.env['mail.mail'].sudo().search([
                ('mail_message_id', 'in', messages.ids),
            ])
            if mails:
                mails.write({'x_microsoft_mailbox_id': self.x_microsoft_send_from_id.id})

        return messages
