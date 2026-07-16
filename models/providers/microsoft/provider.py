# -*- coding: utf-8 -*-
import base64
import logging
from datetime import datetime as dt_datetime

from odoo import models

_logger = logging.getLogger(__name__)

# Graph returns file attachments and reference/item attachments through the same
# list; only file attachments carry contentBytes.
GRAPH_FILE_ATTACHMENT = '#microsoft.graph.fileAttachment'


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

    # -------------------------------------------------------------------------
    # Receiving
    # -------------------------------------------------------------------------
    def _fetch_message_previews(self, mailbox, folder, since=None, limit=50):
        messages = self.env['microsoft.graph.client'].fetch_messages(
            user=mailbox.x_owner_user_id,
            mailbox_email=mailbox.email,
            folder=folder,
            since_datetime=since,
            top=limit,
        )
        return [{
            'message_id': m.get('internetMessageId'),
            'provider_message_id': m.get('id'),
            'date': self._parse_datetime(m.get('receivedDateTime')),
            'subject': m.get('subject', ''),
        } for m in messages]

    def _get_message(self, mailbox, provider_message_id):
        raw = self.env['microsoft.graph.client'].get_message_with_headers(
            user=mailbox.x_owner_user_id,
            mailbox_email=mailbox.email,
            message_id=provider_message_id,
        )
        return self._normalize_message(raw)

    def _get_attachments(self, mailbox, provider_message_id):
        raw_attachments = self.env['microsoft.graph.client'].get_message_attachments(
            user=mailbox.x_owner_user_id,
            mailbox_email=mailbox.email,
            message_id=provider_message_id,
        )

        attachments = []
        for raw in raw_attachments:
            name = raw.get('name', 'unnamed')
            # Reference and item attachments carry no contentBytes; skip both.
            if raw.get('@odata.type', 'unknown') != GRAPH_FILE_ATTACHMENT:
                continue
            content_b64 = raw.get('contentBytes')
            if not content_b64:
                continue
            try:
                content = base64.b64decode(content_b64)
            except Exception as e:
                _logger.warning(f"[Incoming Mail] Failed to process attachment {name}: {e}")
                continue
            is_inline = bool(raw.get('isInline'))
            attachments.append({
                'name': name,
                'content': content,
                'content_type': raw.get('contentType', ''),
                'is_inline': is_inline,
                # An inline attachment without a contentId can't be referenced
                # from the body, so it is treated as a regular file downstream.
                'cid': raw.get('contentId') if is_inline else None,
            })
        return attachments

    # -------------------------------------------------------------------------
    # Normalization — the only place Graph's JSON shape is understood
    # -------------------------------------------------------------------------
    def _normalize_message(self, raw):
        """Turn a Graph message object into the shape in providers/message.py."""
        headers = {
            h['name'].lower(): h['value']
            for h in raw.get('internetMessageHeaders', [])
        }
        body = raw.get('body', {})
        references = headers.get('references', '')

        return {
            'message_id': raw.get('internetMessageId'),
            'provider_message_id': raw.get('id'),
            'thread_id': raw.get('conversationId'),
            'in_reply_to': headers.get('in-reply-to'),
            'references': references.split() if references else [],
            'subject': raw.get('subject', ''),
            'date': self._parse_datetime(raw.get('receivedDateTime')),
            'from': self._parse_address(raw.get('from')),
            'to': [self._parse_address(r) for r in raw.get('toRecipients', [])],
            'cc': [self._parse_address(r) for r in raw.get('ccRecipients', [])],
            'body_html': body.get('content', ''),
            'is_html': body.get('contentType') == 'html',
            'headers': headers,
            # Graph reports False here for messages carrying only inline images.
            'has_attachments': raw.get('hasAttachments', False),
            'attachments': [],
        }

    def _parse_address(self, entry):
        """Graph nests addresses as {'emailAddress': {'name': .., 'address': ..}}."""
        email_address = (entry or {}).get('emailAddress', {})
        return (email_address.get('name', ''), email_address.get('address', ''))

    def _parse_datetime(self, value):
        """Graph ISO-8601 (always Z) -> naive UTC, as the sync cursor expects."""
        if not value:
            return None
        return dt_datetime.fromisoformat(
            value.replace('Z', '+00:00')
        ).replace(tzinfo=None)
