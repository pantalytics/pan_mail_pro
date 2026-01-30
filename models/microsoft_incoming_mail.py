# -*- coding: utf-8 -*-
"""
Incoming Mail Processor for Microsoft Graph API.

This module fetches emails from Microsoft 365 mailboxes and routes them
to the correct partner using message_post() for proper threading.
"""
import base64
import logging
from markupsafe import Markup

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

        # First sync: if x_sync_start_date is set, use it for historical sync
        # Otherwise just test connection and start from now
        if not mailbox.x_last_sync_date:
            if mailbox.x_sync_start_date:
                # Historical sync: start from configured date
                _logger.info(f"[Incoming Mail] First sync for {mailbox.email}, starting from {mailbox.x_sync_start_date}")
                mailbox.write({'x_last_sync_date': mailbox.x_sync_start_date})
                # Continue to fetch messages below
            else:
                # No start date: just test connection and start from now
                _logger.info(f"[Incoming Mail] First sync for {mailbox.email}, testing connection...")
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
        raw_headers = full_message.get('internetMessageHeaders', [])
        headers = {h['name'].lower(): h['value'] for h in raw_headers}
        if headers.get('x-odoo-model') or headers.get('x-odoo-mail-id'):
            _logger.info(f"[Incoming Mail] Skipping Odoo-originated email: {internet_message_id}")
            return False

        # Determine if this is incoming or outgoing (for 2-way sync)
        is_outgoing = folder == 'SentItems'

        # For incoming: use sender (from). For outgoing: use first recipient (to)
        if is_outgoing:
            # Sent Items: get the recipient
            to_recipients = full_message.get('toRecipients', [])
            if not to_recipients:
                _logger.info(f"[Incoming Mail] Skipping sent email without recipients: {internet_message_id}")
                return False
            contact_data = to_recipients[0].get('emailAddress', {})
            contact_email = contact_data.get('address', '')
            contact_name = contact_data.get('name', '')
            _logger.info(f"[Incoming Mail] Sent to: name='{contact_name}', email='{contact_email}'")
        else:
            # Inbox: use the sender
            from_data = full_message.get('from', {}).get('emailAddress', {})
            contact_email = from_data.get('address', '')
            contact_name = from_data.get('name', '')
            _logger.info(f"[Incoming Mail] From: name='{contact_name}', email='{contact_email}'")

        # Apply sync mode filter: "known_partners" mode only syncs from existing contacts
        # For Sent Items, we skip internal domain check (we sent it, we want it synced)
        if mailbox.x_sync_mode == 'known_partners':
            # Skip emails from/to internal domains (only for incoming)
            if not is_outgoing and self._is_internal_domain(contact_email):
                _logger.info(f"[Incoming Mail] Skipping internal domain: {contact_email}")
                return False

            partner = self._find_partner(contact_email)
            if not partner:
                _logger.info(f"[Incoming Mail] Skipping unknown contact (not in Odoo): {contact_email}")
                return False
            if partner.user_ids:
                _logger.info(f"[Incoming Mail] Skipping internal user: {partner.name} ({contact_email})")
                return False
            _logger.info(f"[Incoming Mail] Known partner filter passed: {partner.name}")

        # Get attachments if present
        attachments = []
        has_attachments = full_message.get('hasAttachments', False)
        _logger.info(f"[Incoming Mail] hasAttachments={has_attachments}")
        if has_attachments:
            attachments = graph_client.get_message_attachments(
                user=mailbox.x_owner_user_id,
                mailbox_email=mailbox.email,
                message_id=msg_data['id'],
            )
            _logger.info(f"[Incoming Mail] Fetched {len(attachments)} attachment(s)")

        # Find or create the partner (contact) for chatter posting
        partner = None
        if contact_email:
            partner = self._find_or_create_partner(contact_email, contact_name)
            _logger.info(f"[Incoming Mail] Partner resolved: {partner.name} (id={partner.id}, email={partner.email})")

        if not partner:
            _logger.warning(f"[Incoming Mail] Could not resolve partner for {contact_email}, skipping")
            return False

        # Check for threading: find parent message via In-Reply-To header or conversationId
        # If this is a reply, post to the SAME record as the parent message
        parent_message = False
        target_record = partner  # Default: post to partner's chatter
        in_reply_to = headers.get('in-reply-to')
        conversation_id = full_message.get('conversationId')

        # Try In-Reply-To header first (standard email threading)
        if in_reply_to:
            # First check our custom x_microsoft_message_id field
            # This contains the Microsoft internetMessageId we stored after sending
            parent_message = self.env['mail.message'].search([
                ('x_microsoft_message_id', '=', in_reply_to)
            ], limit=1)

            # Fallback to standard Odoo message_id field
            if not parent_message:
                parent_message = self.env['mail.message'].search([
                    ('message_id', '=', in_reply_to)
                ], limit=1)

        # Fallback to Microsoft conversationId if In-Reply-To didn't work
        # This is especially useful for Sent Items where Graph API may not return headers
        if not parent_message and conversation_id:
            parent_message = self.env['mail.message'].search([
                ('x_microsoft_conversation_id', '=', conversation_id),
                ('model', '!=', False),
                ('res_id', '!=', False),
            ], order='id asc', limit=1)

        if parent_message and parent_message.model and parent_message.res_id:
            # Reply to existing thread - post to the same record
            target_record = self.env[parent_message.model].browse(parent_message.res_id)
            _logger.info(f"[Incoming Mail] Threading reply to {parent_message.model}/{parent_message.res_id}")

        # For new incoming emails (not replies, not sent items), create CRM Lead
        if not parent_message and not is_outgoing:
            target_record = self._get_or_create_lead_for_email(
                partner=partner,
                subject=full_message.get('subject', ''),
                contact_email=contact_email,
            )

        # Prepare attachment data for message_post
        # Inline attachments (embedded images) use 3-tuple format so Odoo converts cid: to /web/image/
        # Regular attachments use ir.attachment records
        attachment_ids = []
        inline_attachments = []  # 3-tuples: (filename, content, {'cid': content_id})

        if attachments:
            for attachment in attachments:
                att_type = attachment.get('@odata.type', 'unknown')
                att_name = attachment.get('name', 'unnamed')
                is_inline = attachment.get('isInline', False)
                content_id = attachment.get('contentId')
                _logger.info(f"[Incoming Mail] Attachment: {att_name}, type={att_type}, isInline={is_inline}, contentId={content_id}")

                if att_type == '#microsoft.graph.fileAttachment':
                    content_bytes_b64 = attachment.get('contentBytes')
                    if content_bytes_b64:
                        name = att_name

                        if is_inline and content_id:
                            # Inline attachment: use 3-tuple format for Odoo's cid: conversion
                            try:
                                content_binary = base64.b64decode(content_bytes_b64)
                                inline_attachments.append((name, content_binary, {'cid': content_id}))
                                _logger.info(f"[Incoming Mail] Inline attachment: {name} (cid:{content_id})")
                            except Exception as e:
                                _logger.warning(f"[Incoming Mail] Failed to decode inline attachment {name}: {e}")
                        else:
                            # Regular attachment: create ir.attachment record
                            try:
                                att = self.env['ir.attachment'].create({
                                    'name': name,
                                    'datas': content_bytes_b64,  # Already base64 encoded
                                    'res_model': target_record._name,
                                    'res_id': target_record.id,
                                })
                                attachment_ids.append(att.id)
                            except Exception as e:
                                _logger.warning(f"[Incoming Mail] Failed to create attachment: {e}")

        # Build email body - mark as safe HTML to preserve formatting
        body = full_message.get('body', {})
        body_content = body.get('content', '')
        if body.get('contentType') == 'html' and body_content:
            body_content = Markup(body_content)

        # Determine author: for incoming it's the contact, for outgoing it depends on mailbox type
        if is_outgoing:
            from_data = full_message.get('from', {}).get('emailAddress', {})
            author_email = from_data.get('address', mailbox.email)
            author_name = from_data.get('name', '')

            if mailbox.x_mailbox_type == 'shared':
                # Shared mailbox: use a partner for the mailbox itself (e.g., "team1" for team1@company.com)
                author = self._find_or_create_partner(mailbox.email)
                _logger.info(f"[Incoming Mail] Shared mailbox author: {author.name}")
            else:
                # Personal/notification: use the owner's partner
                author = mailbox.x_owner_user_id.partner_id
        else:
            # Received email: author is the sender (the contact)
            author = partner
            author_email = contact_email
            author_name = contact_name

        # Post message to the target record:
        # - If reply: post to the same record as the parent (sale.order, lead, etc.)
        # - If new incoming: post to a new CRM Lead (or partner if CRM not installed)
        # - If sent item: post to the contact's chatter
        try:
            message = target_record.message_post(
                body=body_content,
                subject=full_message.get('subject', ''),
                message_type='email',
                subtype_xmlid='mail.mt_comment',
                author_id=author.id,
                email_from=f'"{author_name}" <{author_email}>' if author_name else author_email,
                message_id=internet_message_id,
                parent_id=parent_message.id if parent_message else False,
                attachment_ids=attachment_ids,
                attachments=inline_attachments,  # 3-tuples for inline image cid: conversion
            )

            # Store Microsoft conversationId for threading future replies
            # This is crucial because Graph API doesn't always return In-Reply-To headers
            if conversation_id:
                message.write({'x_microsoft_conversation_id': conversation_id})
                _logger.info(f"[Incoming Mail] Stored conversationId: {conversation_id}")

            # Create activity for new emails (only for incoming, not sent items, and only for new threads)
            if mailbox.x_create_activity and not is_outgoing and not parent_message:
                self._create_review_activity(mailbox, target_record)

            _logger.info(f"[Incoming Mail] Successfully processed: {internet_message_id} -> {target_record._name}/{target_record.id}")
            return True

        except Exception as e:
            _logger.exception(f"[Incoming Mail] Failed to process message: {internet_message_id}")
            raise

    def _create_review_activity(self, mailbox, target_record):
        """
        Create an activity for the mailbox owner to review a new email.

        Args:
            mailbox: x_microsoft.mailbox record
            target_record: The record the message was posted to (crm.lead or res.partner)
        """
        if not mailbox.x_owner_user_id:
            return  # No owner, skip activity creation

        # Ensure we have valid model and id
        model_name = target_record._name
        record_id = target_record.id
        if not model_name or not record_id:
            _logger.warning(f"[Incoming Mail] Cannot create activity: model={model_name}, id={record_id}")
            return

        try:
            self.env['mail.activity'].create({
                'res_model_id': self.env['ir.model']._get_id(model_name),
                'res_id': record_id,
                'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
                'summary': _('Review incoming email from %s') % mailbox.email,
                'user_id': mailbox.x_owner_user_id.id,
                'date_deadline': fields.Date.today(),
            })
            _logger.info(f"[Incoming Mail] Created activity on {model_name}/{record_id} for {mailbox.x_owner_user_id.name}")
        except Exception as e:
            _logger.warning(f"[Incoming Mail] Failed to create activity: {e}")

    def _is_duplicate(self, internet_message_id):
        """
        Check if a message with this ID already exists in Odoo.

        Checks both:
        1. mail.message.message_id - for messages already imported
        2. mail.mail.x_microsoft_message_id - for emails sent via our module

        This prevents re-importing Sent Items that were sent from Odoo.

        Args:
            internet_message_id: The Microsoft internetMessageId

        Returns:
            bool: True if duplicate exists
        """
        if not internet_message_id:
            return False

        # Check mail.message (already imported messages)
        if self.env['mail.message'].search([
            ('message_id', '=', internet_message_id)
        ], limit=1):
            return True

        # Check mail.mail (emails sent via our module)
        if self.env['mail.mail'].search([
            ('x_microsoft_message_id', '=', internet_message_id)
        ], limit=1):
            _logger.info(f"[Incoming Mail] Skipping Odoo-sent email (found in mail.mail): {internet_message_id}")
            return True

        return False

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

    def _get_or_create_lead_for_email(self, partner, subject, contact_email):
        """
        Create a CRM Lead for a new incoming email.

        For new emails (not replies), we create a CRM Lead to track the conversation.
        This enables AI-powered customer summaries by aggregating leads per company.

        Args:
            partner: res.partner record for the sender
            subject: Email subject
            contact_email: Sender email address

        Returns:
            crm.lead record
        """
        lead_name = subject if subject else f"Email from {partner.name}"

        lead = self.env['crm.lead'].create({
            'name': lead_name,
            'partner_id': partner.id,
            'email_from': contact_email,
            'type': 'lead',
        })

        _logger.info(f"[Incoming Mail] Created CRM Lead: {lead.name} (id={lead.id})")
        return lead
