# -*- coding: utf-8 -*-
"""Google Workspace provider, backed by the Gmail REST API.

Thin like the Microsoft one: `gmail.client` does the wire work, this translates
at the boundary so no caller above sees a Gmail JSON key. Send and receive land
in later steps; the credential resolution and capabilities are here first
because they are what dispatch and the OAuth flow need.
"""
import base64
import logging
from datetime import datetime, timezone
from email.utils import getaddresses, parseaddr

from odoo import models, _

_logger = logging.getLogger(__name__)

# The processor speaks Odoo folder names; Gmail speaks system labels.
FOLDER_TO_LABEL = {'Inbox': 'INBOX', 'SentItems': 'SENT'}


class PanMailProviderGoogle(models.AbstractModel):
    _name = 'pan.mail.provider.google'
    _inherit = 'pan.mail.provider.base'
    _description = 'Google Workspace Provider (Gmail API)'

    # -------------------------------------------------------------------------
    # Credentials
    # -------------------------------------------------------------------------
    def _account_for_user(self, user):
        return self.env['pan.mail.account']._for_user(user, 'google')

    def _get_sending_account(self, mailbox, mail):
        """Which Google credentials send `mail` from `mailbox`.

        Unlike Microsoft — where a shared mailbox is sent with the author's own
        token via SendAs — a Gmail shared mailbox is its OWN Workspace account:
        one refresh token, no user behind it, authorized once. So a shared
        mailbox sends with its service account (a pan.mail.account whose email is
        the mailbox address and whose user_id is null), and personal/notification
        mailboxes send with the owner's account. This is the shape the Phase 2
        data model was built for.
        """
        mail.ensure_one()
        if mailbox.x_mailbox_type == 'shared':
            return self._service_account(mailbox)
        return self._account_for_user(mailbox.x_owner_user_id)

    def _service_account(self, mailbox):
        """The mailbox's own Gmail account — user_id null, keyed on the address."""
        return self.env['pan.mail.account'].sudo().with_context(active_test=False).search([
            ('provider', '=', 'google'),
            ('user_id', '=', False),
            ('email', '=', mailbox.email),
        ], limit=1)

    def _supported_mailbox_types(self):
        # All three work: personal/notification via the owner's account, shared
        # via a service account. 'shared' means something different here than on
        # Microsoft (a real account, not SendAs) but the mailbox type is the same.
        return ['personal', 'shared', 'notification']

    # -------------------------------------------------------------------------
    # Sending
    # -------------------------------------------------------------------------
    def _send(self, mail, mailbox, account):
        # The client already returns the neutral shape, so this is a pass-through
        # rather than the key-translation the Graph provider does. Kept explicit
        # so the interface method has one obvious home.
        return self.env['gmail.client'].send_email(mail, mailbox, account)

    # -------------------------------------------------------------------------
    # Receiving
    # -------------------------------------------------------------------------
    def _fetch_message_previews(self, mailbox, folder, since=None, limit=50):
        """Light previews, oldest first, as the datetime-cursor contract demands.

        Gmail's list gives only ids, so each preview costs a metadata fetch to
        recover the Message-ID and date. That is more calls than Graph (whose
        list carries both), but still far cheaper than the full body the
        processor fetches only for messages that survive the dedup/skip checks.
        """
        client = self.env['gmail.client']
        account = self._account_for_user(mailbox.x_owner_user_id)
        label = FOLDER_TO_LABEL.get(folder, folder.upper())
        after_epoch = since.replace(tzinfo=timezone.utc).timestamp() if since else None

        previews = []
        for entry in client.list_message_ids(account, label, after_epoch, limit):
            raw = client.get_message(account, entry['id'], fmt='metadata',
                                     headers=['Message-Id', 'Subject'])
            headers = self._headers_dict(raw.get('payload', {}))
            previews.append({
                'message_id': headers.get('message-id'),
                'provider_message_id': raw.get('id'),
                'date': self._gmail_date(raw.get('internalDate')),
                'subject': headers.get('subject', ''),
            })
        # Gmail returns newest-first; the sync cursor advances on ascending date.
        previews.sort(key=lambda p: p['date'] or datetime.min)
        return previews

    def _get_message(self, mailbox, provider_message_id):
        account = self._account_for_user(mailbox.x_owner_user_id)
        raw = self.env['gmail.client'].get_message(account, provider_message_id, fmt='full')
        return self._normalize_message(raw)

    def _get_attachments(self, mailbox, provider_message_id):
        client = self.env['gmail.client']
        account = self._account_for_user(mailbox.x_owner_user_id)
        raw = client.get_message(account, provider_message_id, fmt='full')

        attachments = []
        for part in self._walk(raw.get('payload', {})):
            filename = part.get('filename')
            if not filename:
                continue
            body = part.get('body', {})
            if body.get('data'):
                content = base64.urlsafe_b64decode(body['data'])
            elif body.get('attachmentId'):
                content = client.get_attachment_data(
                    account, provider_message_id, body['attachmentId'])
            else:
                continue
            if not content:
                continue
            part_headers = {h['name'].lower(): h['value'] for h in part.get('headers', [])}
            content_id = part_headers.get('content-id', '')
            # Inline if the sender said so or gave it a Content-Id to reference
            # from the body - same test the Graph provider applies.
            is_inline = 'inline' in part_headers.get('content-disposition', '').lower() or bool(content_id)
            attachments.append({
                'name': filename,
                'content': content,
                'content_type': part.get('mimeType', ''),
                'is_inline': is_inline,
                'cid': content_id.strip('<>') if (is_inline and content_id) else None,
            })
        return attachments

    # -------------------------------------------------------------------------
    # Normalization — the only place Gmail's JSON shape is understood
    # -------------------------------------------------------------------------
    def _normalize_message(self, raw):
        """Turn a Gmail message object into the shape in providers/message.py."""
        payload = raw.get('payload', {})
        headers = self._headers_dict(payload)
        references = headers.get('references', '')
        body_html, is_html = self._extract_body(payload)

        return {
            'message_id': headers.get('message-id'),
            'provider_message_id': raw.get('id'),
            'thread_id': raw.get('threadId'),
            'in_reply_to': headers.get('in-reply-to'),
            'references': references.split() if references else [],
            'subject': headers.get('subject', ''),
            'date': self._gmail_date(raw.get('internalDate')),
            'from': parseaddr(headers.get('from', '')),
            'to': self._parse_address_list(headers.get('to')),
            'cc': self._parse_address_list(headers.get('cc')),
            'body_html': body_html,
            'is_html': is_html,
            'headers': headers,
            'has_attachments': any(p.get('filename') for p in self._walk(payload)),
            'attachments': [],
        }

    def _headers_dict(self, payload):
        """All header names lowercased, as message.py requires."""
        return {h['name'].lower(): h['value'] for h in payload.get('headers', [])}

    def _walk(self, part):
        """Depth-first over a MIME tree; Gmail nests parts within parts."""
        yield part
        for sub in part.get('parts', []) or []:
            yield from self._walk(sub)

    def _extract_body(self, payload):
        """Prefer text/html; fall back to text/plain. Returns (content, is_html)."""
        html = plain = None
        for part in self._walk(payload):
            if part.get('filename'):
                continue  # an attachment, not the body
            data = part.get('body', {}).get('data')
            if not data:
                continue
            mime = part.get('mimeType', '')
            if mime == 'text/html' and html is None:
                html = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
            elif mime == 'text/plain' and plain is None:
                plain = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
        if html is not None:
            return html, True
        return (plain or ''), False

    def _parse_address_list(self, value):
        return [(name, email) for name, email in getaddresses([value or '']) if email]

    def _gmail_date(self, internal_date):
        """Gmail internalDate is epoch milliseconds -> naive UTC, as the cursor wants."""
        if not internal_date:
            return None
        return datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc).replace(tzinfo=None)
