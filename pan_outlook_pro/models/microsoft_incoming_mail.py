# -*- coding: utf-8 -*-
"""
Incoming Mail Processor for Microsoft Graph API.

This module fetches emails from Microsoft 365 mailboxes and routes them
through Odoo's native mail.thread.message_process() for proper threading
and partner linking.
"""
import base64
import logging
from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime

from odoo import models, api, fields, _

_logger = logging.getLogger(__name__)


class MicrosoftIncomingMailProcessor(models.AbstractModel):
    """
    Processor for incoming emails from Microsoft Graph API.

    Uses Odoo's native mail.thread.message_process() for routing,
    which provides automatic threading, partner creation, and chatter integration.
    """
    _name = 'microsoft.incoming.mail.processor'
    _description = 'Microsoft Incoming Mail Processor'

    @api.model
    def _cron_fetch_incoming_mail(self):
        """
        Cron method to fetch emails from all enabled mailboxes.
        Called by ir.cron every 5 minutes.
        """
        mailboxes = self.env['x_microsoft.mailbox'].search([
            ('x_incoming_enabled', '=', True),
            ('x_incoming_user_id', '!=', False),
            ('state', 'in', ['active', 'draft']),  # Also try draft to auto-activate
        ])

        _logger.info(f"[Incoming Mail] Starting sync for {len(mailboxes)} mailbox(es)")

        for mailbox in mailboxes:
            try:
                self._process_mailbox(mailbox)
                # Mark as active if successful
                if mailbox.state != 'active':
                    mailbox.write({'state': 'active', 'x_error_message': False})
            except Exception as e:
                _logger.exception(f"[Incoming Mail] Error processing mailbox {mailbox.email}")
                mailbox.write({
                    'state': 'error',
                    'x_error_message': str(e),
                })

        _logger.info("[Incoming Mail] Sync completed")

    def _process_mailbox(self, mailbox):
        """
        Fetch and process messages for a single mailbox.

        Args:
            mailbox: x_microsoft.mailbox record
        """
        _logger.info(f"[Incoming Mail] Processing mailbox: {mailbox.email}")

        # First sync: set last_sync_date to now to avoid fetching old emails
        if not mailbox.x_last_sync_date:
            _logger.info(f"[Incoming Mail] First sync for {mailbox.email}, setting sync date to now (no old emails will be fetched)")
            mailbox.write({'x_last_sync_date': fields.Datetime.now()})
            return  # Skip this run, start fetching from next cron run

        processed_count = 0

        # Fetch from Inbox
        if mailbox.x_sync_inbox:
            count = self._fetch_folder(mailbox, 'Inbox')
            processed_count += count

        # Fetch from Sent Items (2-way sync)
        if mailbox.x_sync_sent:
            count = self._fetch_folder(mailbox, 'SentItems')
            processed_count += count

        # Update last sync date
        mailbox.write({'x_last_sync_date': fields.Datetime.now()})

        _logger.info(f"[Incoming Mail] Processed {processed_count} message(s) from {mailbox.email}")

    def _fetch_folder(self, mailbox, folder):
        """
        Fetch messages from a specific folder.

        Args:
            mailbox: x_microsoft.mailbox record
            folder: Folder name ('Inbox', 'SentItems', etc.)

        Returns:
            int: Number of messages processed
        """
        graph_client = self.env['microsoft.graph.client']

        # Fetch messages since last sync
        messages = graph_client.fetch_messages(
            user=mailbox.x_incoming_user_id,
            mailbox_email=mailbox.email,
            folder=folder,
            since_datetime=mailbox.x_last_sync_date,
            top=50,  # Process in batches
        )

        processed = 0
        for msg_data in messages:
            try:
                if self._process_message(mailbox, msg_data, folder):
                    processed += 1
            except Exception as e:
                _logger.exception(f"[Incoming Mail] Error processing message {msg_data.get('id')}")
                # Continue with next message

        return processed

    def _process_message(self, mailbox, msg_data, folder):
        """
        Process a single message using Odoo's native routing.

        Args:
            mailbox: x_microsoft.mailbox record
            msg_data: Message data from Graph API (preview)
            folder: Folder name

        Returns:
            bool: True if message was processed, False if skipped
        """
        internet_message_id = msg_data.get('internetMessageId')

        # Check for duplicate
        if self._is_duplicate(internet_message_id):
            _logger.debug(f"[Incoming Mail] Skipping duplicate: {internet_message_id}")
            return False

        _logger.info(f"[Incoming Mail] Processing: {msg_data.get('subject', '(no subject)')}")

        # Get full message with headers for threading
        graph_client = self.env['microsoft.graph.client']
        full_message = graph_client.get_message_with_headers(
            user=mailbox.x_incoming_user_id,
            mailbox_email=mailbox.email,
            message_id=msg_data['id'],
        )

        # Get attachments if present
        attachments = []
        if full_message.get('hasAttachments'):
            attachments = graph_client.get_message_attachments(
                user=mailbox.x_incoming_user_id,
                mailbox_email=mailbox.email,
                message_id=msg_data['id'],
            )

        # Determine if this is incoming or outgoing (for 2-way sync)
        is_outgoing = folder == 'SentItems'

        # Pre-create or find partner BEFORE message_process to ensure correct name/email
        # This prevents message_process from creating partner with wrong name
        from_data = full_message.get('from', {}).get('emailAddress', {})
        from_email = from_data.get('address', '')
        from_name = from_data.get('name', '')

        _logger.info(f"[Incoming Mail] From: name='{from_name}', email='{from_email}'")

        partner = None
        if from_email:
            partner = self._find_or_create_partner(from_email, from_name)
            _logger.info(f"[Incoming Mail] Partner resolved: {partner.name} (id={partner.id}, email={partner.email})")

        # Convert to RFC2822 format
        rfc2822_msg = self._convert_to_rfc2822(full_message, attachments)

        # Use Odoo's native message_process for routing
        MailThread = self.env['mail.thread']
        try:
            thread_id = MailThread.message_process(
                model='res.partner',  # Default: route to partner
                message=rfc2822_msg,
                save_original=False,
                strip_attachments=False,
            )

            # Create activity for new emails (only for incoming, not sent items)
            if mailbox.x_create_activity and thread_id and not is_outgoing:
                self._create_review_activity(mailbox, thread_id)

            _logger.info(f"[Incoming Mail] Successfully processed: {internet_message_id} -> {thread_id}")
            return True

        except Exception as e:
            _logger.exception(f"[Incoming Mail] Failed to process message: {internet_message_id}")
            raise

    def _convert_to_rfc2822(self, graph_message, attachments=None):
        """
        Convert Microsoft Graph message to RFC2822 email bytes.

        This conversion preserves the headers needed for Odoo's routing:
        - Message-ID for duplicate detection
        - In-Reply-To and References for threading
        - From/To/Cc for partner matching

        Args:
            graph_message: Full message dict from Graph API
            attachments: List of attachment dicts from Graph API

        Returns:
            bytes: RFC2822 formatted email message
        """
        msg = EmailMessage()

        # Essential headers for Odoo routing
        internet_message_id = graph_message.get('internetMessageId')
        if internet_message_id:
            msg['Message-ID'] = internet_message_id

        msg['Subject'] = graph_message.get('subject', '')

        # From address
        from_data = graph_message.get('from', {}).get('emailAddress', {})
        msg['From'] = self._format_address(from_data)

        # To recipients
        to_recipients = graph_message.get('toRecipients', [])
        if to_recipients:
            msg['To'] = ', '.join(
                self._format_address(r.get('emailAddress', {}))
                for r in to_recipients
            )

        # CC recipients
        cc_recipients = graph_message.get('ccRecipients', [])
        if cc_recipients:
            msg['Cc'] = ', '.join(
                self._format_address(r.get('emailAddress', {}))
                for r in cc_recipients
            )

        # Date
        received_dt = graph_message.get('receivedDateTime')
        if received_dt:
            # Parse ISO format and format for email
            try:
                dt = datetime.fromisoformat(received_dt.replace('Z', '+00:00'))
                msg['Date'] = format_datetime(dt)
            except (ValueError, TypeError):
                pass

        # Critical: threading headers from internetMessageHeaders
        # These are essential for Odoo to route replies correctly
        headers = {}
        for header in graph_message.get('internetMessageHeaders', []):
            headers[header['name'].lower()] = header['value']

        if headers.get('in-reply-to'):
            msg['In-Reply-To'] = headers['in-reply-to']
        if headers.get('references'):
            msg['References'] = headers['references']

        # Body
        body = graph_message.get('body', {})
        body_content = body.get('content', '')
        if body.get('contentType') == 'html':
            msg.set_content(body_content, subtype='html')
        else:
            msg.set_content(body_content)

        # Attachments
        if attachments:
            for attachment in attachments:
                if attachment.get('@odata.type') == '#microsoft.graph.fileAttachment':
                    content_bytes = attachment.get('contentBytes')
                    if content_bytes:
                        try:
                            file_content = base64.b64decode(content_bytes)
                            content_type = attachment.get('contentType', 'application/octet-stream')

                            # Parse content type
                            if '/' in content_type:
                                maintype, subtype = content_type.split('/', 1)
                            else:
                                maintype, subtype = 'application', 'octet-stream'

                            msg.add_attachment(
                                file_content,
                                maintype=maintype,
                                subtype=subtype,
                                filename=attachment.get('name', 'attachment'),
                            )
                        except Exception as e:
                            _logger.warning(f"[Incoming Mail] Failed to add attachment: {e}")

        return msg.as_bytes()

    def _create_review_activity(self, mailbox, partner_id):
        """
        Create an activity for the team to review a new email.

        Args:
            mailbox: x_microsoft.mailbox record
            partner_id: ID of the partner the message was posted to
        """
        if not mailbox.x_activity_user_id:
            return  # No user assigned, skip activity creation

        try:
            self.env['mail.activity'].create({
                'res_model': 'res.partner',
                'res_id': partner_id,
                'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
                'summary': _('Review incoming email from %s') % mailbox.email,
                'user_id': mailbox.x_activity_user_id.id,
                'date_deadline': fields.Date.today(),
            })
            _logger.debug(f"[Incoming Mail] Created activity for partner {partner_id}")
        except Exception as e:
            _logger.warning(f"[Incoming Mail] Failed to create activity: {e}")

    def _is_duplicate(self, internet_message_id):
        """
        Check if a message with this ID already exists in Odoo.

        Args:
            internet_message_id: The Message-ID header value

        Returns:
            bool: True if duplicate exists
        """
        if not internet_message_id:
            return False

        return bool(self.env['mail.message'].search([
            ('message_id', '=', internet_message_id)
        ], limit=1))

    def _find_or_create_partner(self, email, name=None):
        """
        Find existing partner by email or create a new one.

        This ensures partners are created with correct name and email
        BEFORE message_process runs, which prevents Odoo from using
        the email subject as the partner name.

        Args:
            email: Email address to search/create
            name: Display name for new partner (optional)

        Returns:
            res.partner record
        """
        Partner = self.env['res.partner']

        # Normalize email for search
        email_normalized = email.lower().strip()

        # Search for existing partner by email
        partner = Partner.search([
            '|',
            ('email', '=ilike', email_normalized),
            ('email_normalized', '=', email_normalized),
        ], limit=1)

        if partner:
            _logger.debug(f"[Incoming Mail] Found existing partner: {partner.name} for {email}")
            return partner

        # Create new partner with correct name and email
        partner_name = name if name else email.split('@')[0]  # Use local part as fallback
        partner = Partner.create({
            'name': partner_name,
            'email': email,
            'is_company': False,
        })
        _logger.info(f"[Incoming Mail] Created new partner: {partner.name} ({email})")

        return partner

    def _format_address(self, email_dict):
        """
        Format a Graph API email address dict to RFC2822 format.

        Args:
            email_dict: {'name': '...', 'address': '...'}

        Returns:
            str: RFC2822 formatted address like '"Name" <email@example.com>'
        """
        name = email_dict.get('name', '')
        address = email_dict.get('address', '')

        if not address:
            return ''

        if name:
            # Escape quotes in name
            name = name.replace('"', '\\"')
            return f'"{name}" <{address}>'

        return address
