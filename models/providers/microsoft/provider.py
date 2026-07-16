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

    def _get_sending_account(self, mailbox, mail):
        """Pick the OAuth-connected user whose token should send `mail`.

        Personal and notification mailboxes have exactly one viable token
        holder: the owner. Shared mailboxes are the interesting case - a
        Microsoft user with SendAs rights sends with their *own* token
        (Mail.Send.Shared), so the answer depends on who wrote the mail.

        Prefer the author over env.user: in cron context env.user is the cron
        runner, not the sender.
        """
        mail.ensure_one()
        if mailbox.x_mailbox_type in ('notification', 'personal'):
            return mailbox.x_owner_user_id
        if mail.author_id and mail.author_id.user_ids:
            return mail.author_id.user_ids[0]
        env_user = self.env.user
        if env_user and not env_user._is_public():
            return env_user
        return self.env['res.users']
