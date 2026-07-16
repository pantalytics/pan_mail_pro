# -*- coding: utf-8 -*-
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class PanMailProviderMicrosoft(models.AbstractModel):
    """Microsoft 365 provider, backed by the Graph API.

    Thin by design: `microsoft.graph.client` keeps doing the work, this only
    translates at the boundary so callers never see a Graph-native key.
    """
    _name = 'pan.mail.provider.microsoft'
    _inherit = 'pan.mail.provider.base'
    _description = 'Microsoft 365 Provider (Graph API)'

    def _send(self, mail, mailbox, account):
        result = self.env['microsoft.graph.client'].send_email_via_graph(
            mail_record=mail,
            mailbox=mailbox,
            user=account,
        )
        return {
            'success': result['success'],
            'error': result.get('error'),
            'error_code': result.get('error_code'),
            'message_id': result.get('microsoft_message_id'),
            'thread_id': result.get('microsoft_conversation_id'),
        }
