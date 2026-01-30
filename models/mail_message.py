# -*- coding: utf-8 -*-
"""
Extension of mail.message for Microsoft Graph conversation threading.

Microsoft Graph API doesn't always return In-Reply-To headers, especially for
Sent Items in shared mailboxes. We use Microsoft's conversationId for threading
as a fallback.
"""
from odoo import fields, models


class MailMessage(models.Model):
    """Extend mail.message with Microsoft conversation tracking"""
    _inherit = 'mail.message'

    x_microsoft_message_id = fields.Char(
        string='Microsoft Message ID',
        help='Microsoft Graph internetMessageId - used for In-Reply-To threading',
        index=True,
    )

    x_microsoft_conversation_id = fields.Char(
        string='Microsoft Conversation ID',
        help='Microsoft Graph conversationId for email threading',
        index=True,
    )
