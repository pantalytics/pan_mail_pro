# -*- coding: utf-8 -*-
"""
Incoming Mail Processor for Microsoft Graph API.

This module fetches emails from Microsoft 365 mailboxes and routes them
to the correct partner using message_post() for proper threading.
"""
import logging

from odoo import models, api, fields, _

_logger = logging.getLogger(__name__)


class MicrosoftIncomingMailProcessor(models.AbstractModel):
    """
    Processor for incoming emails from Microsoft Graph API.

    Posts messages directly to partners using message_post(),
    ensuring proper partner matching and message threading.
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
            ('x_owner_user_id', '!=', False),
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

        # First sync: test connection, then set last_sync_date to now
        if not mailbox.x_last_sync_date:
            _logger.info(f"[Incoming Mail] First sync for {mailbox.email}, testing connection...")
            # Test connection by fetching 1 message (don't import, just verify access)
            graph_client = self.env['microsoft.graph.client']
            graph_client.fetch_messages(
                user=mailbox.x_owner_user_id,
                mailbox_email=mailbox.email,
                folder='Inbox',
                top=1,  # Just test, don't fetch all
            )
            _logger.info(f"[Incoming Mail] Connection test passed for {mailbox.email}, setting sync date to now")
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
            user=mailbox.x_owner_user_id,
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
            user=mailbox.x_owner_user_id,
            mailbox_email=mailbox.email,
            message_id=msg_data['id'],
        )

        # Check for Odoo-originated emails (skip to prevent import loops)
        # We add X-Odoo-* headers to all outgoing emails
        headers = {h['name'].lower(): h['value']
                   for h in full_message.get('internetMessageHeaders', [])}
        if headers.get('x-odoo-model') or headers.get('x-odoo-mail-id'):
            _logger.info(f"[Incoming Mail] Skipping Odoo-originated email: {internet_message_id}")
            return False

        # Extract sender info for filtering and partner matching
        from_data = full_message.get('from', {}).get('emailAddress', {})
        from_email = from_data.get('address', '')
        from_name = from_data.get('name', '')

        _logger.info(f"[Incoming Mail] From: name='{from_name}', email='{from_email}'")

        # Apply sync mode filter: "known_partners" mode only syncs from existing contacts
        if mailbox.x_sync_mode == 'known_partners':
            # Skip emails from internal domains (company employees)
            if self._is_internal_domain(from_email):
                _logger.info(f"[Incoming Mail] Skipping internal domain: {from_email}")
                return False

            partner = self._find_partner(from_email)
            if not partner:
                _logger.info(f"[Incoming Mail] Skipping unknown sender (not in contacts): {from_email}")
                return False
            if partner.user_ids:
                _logger.info(f"[Incoming Mail] Skipping internal user: {partner.name} ({from_email})")
                return False
            _logger.info(f"[Incoming Mail] Known partner filter passed: {partner.name}")

        # Get attachments if present
        attachments = []
        if full_message.get('hasAttachments'):
            attachments = graph_client.get_message_attachments(
                user=mailbox.x_owner_user_id,
                mailbox_email=mailbox.email,
                message_id=msg_data['id'],
            )

        # Determine if this is incoming or outgoing (for 2-way sync)
        is_outgoing = folder == 'SentItems'

        # Find or create the partner (sender) for author_id
        partner = None
        if from_email:
            partner = self._find_or_create_partner(from_email, from_name)
            _logger.info(f"[Incoming Mail] Partner resolved: {partner.name} (id={partner.id}, email={partner.email})")

        if not partner:
            _logger.warning(f"[Incoming Mail] Could not resolve partner for {from_email}, skipping")
            return False

        # Check for threading: find parent message via In-Reply-To header
        # If this is a reply, post to the SAME record as the parent message
        parent_message = False
        target_record = partner  # Default: post to partner's chatter
        in_reply_to = headers.get('in-reply-to')

        if in_reply_to:
            parent_message = self.env['mail.message'].search([
                ('message_id', '=', in_reply_to)
            ], limit=1)
            if parent_message and parent_message.model and parent_message.res_id:
                # Reply to existing thread - post to the same record
                target_record = self.env[parent_message.model].browse(parent_message.res_id)
                _logger.info(f"[Incoming Mail] Reply to {parent_message.model}/{parent_message.res_id}")

        # Prepare attachment data for message_post
        attachment_ids = []
        if attachments:
            for attachment in attachments:
                if attachment.get('@odata.type') == '#microsoft.graph.fileAttachment':
                    content_bytes = attachment.get('contentBytes')
                    if content_bytes:
                        try:
                            att = self.env['ir.attachment'].create({
                                'name': attachment.get('name', 'attachment'),
                                'datas': content_bytes,  # Already base64 encoded
                                'res_model': target_record._name,
                                'res_id': target_record.id,
                            })
                            attachment_ids.append(att.id)
                        except Exception as e:
                            _logger.warning(f"[Incoming Mail] Failed to create attachment: {e}")

        # Build email body
        body = full_message.get('body', {})
        body_content = body.get('content', '')

        # Post message to the target record:
        # - If reply: post to the same record as the parent (sale.order, lead, etc.)
        # - If new: post to the contact's chatter
        try:
            message = target_record.message_post(
                body=body_content,
                subject=full_message.get('subject', ''),
                message_type='email',
                subtype_xmlid='mail.mt_comment',
                author_id=partner.id,  # The sender is the author
                email_from=f'"{from_name}" <{from_email}>' if from_name else from_email,
                message_id=internet_message_id,
                parent_id=parent_message.id if parent_message else False,
                attachment_ids=attachment_ids,
            )

            # Create activity for new emails (only for incoming, not sent items, and only for new threads)
            if mailbox.x_create_activity and not is_outgoing and not parent_message:
                self._create_review_activity(mailbox, partner.id)

            _logger.info(f"[Incoming Mail] Successfully processed: {internet_message_id} -> {target_record._name}/{target_record.id}")
            return True

        except Exception as e:
            _logger.exception(f"[Incoming Mail] Failed to process message: {internet_message_id}")
            raise

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

    def _is_internal_domain(self, email):
        """
        Check if email is from an internal company domain.

        Uses the 'Internal Domains' setting from Outlook Pro configuration.

        Args:
            email: Email address to check

        Returns:
            bool: True if email is from an internal domain
        """
        if not email or '@' not in email:
            return False

        sender_domain = email.lower().split('@')[1]

        # Get internal domains from settings (comma-separated)
        domains_str = self.env['ir.config_parameter'].sudo().get_param(
            'x_pan_outlook_pro.internal_domains', ''
        )
        if not domains_str:
            return False

        # Parse comma-separated domains and normalize
        internal_domains = [d.strip().lower() for d in domains_str.split(',') if d.strip()]

        return sender_domain in internal_domains

    def _find_partner(self, email):
        """
        Find existing partner by email (without creating).

        Used for sync mode filtering - only sync emails from known contacts.

        Args:
            email: Email address to search

        Returns:
            res.partner record or False if not found
        """
        if not email:
            return False

        Partner = self.env['res.partner']
        email_normalized = email.lower().strip()

        return Partner.search([
            '|',
            ('email', '=ilike', email_normalized),
            ('email_normalized', '=', email_normalized),
        ], limit=1)

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
        # First try to find existing partner
        partner = self._find_partner(email)
        if partner:
            _logger.debug(f"[Incoming Mail] Found existing partner: {partner.name} for {email}")
            return partner

        # Create new partner with correct name and email
        partner_name = name if name else email.split('@')[0]  # Use local part as fallback
        partner = self.env['res.partner'].create({
            'name': partner_name,
            'email': email,
            'is_company': False,
        })
        _logger.info(f"[Incoming Mail] Created new partner: {partner.name} ({email})")

        return partner
