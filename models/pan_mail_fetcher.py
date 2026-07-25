# -*- coding: utf-8 -*-
"""
Incoming Mail Processor.

Fetches emails from a mailbox via its provider and routes them to the correct
partner, using message_post()/message_new() for proper threading.

Provider-neutral: everything here reads normalized messages (see
providers/message.py). No Graph, IMAP or other wire-specific shape should ever
appear below this line.
"""
import logging
from markupsafe import Markup

from odoo import models, api, fields

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
        Called by ir.cron every 1 minute.
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
                mailbox._get_provider()._fetch_message_previews(
                    mailbox, 'Inbox', limit=1,  # Just test, don't fetch all
                )
                _logger.info(f"[Incoming Mail] Connection test passed for {mailbox.email}, setting sync date to now")
                mailbox.write({'x_last_sync_date': fields.Datetime.now()})
                return  # Skip this run, start fetching from next cron run

        processed_count = 0
        folder_cursors = []

        # Fetch from Inbox
        if mailbox.x_sync_inbox:
            count, latest_dt = self._fetch_folder(mailbox, 'Inbox')
            processed_count += count
            if latest_dt:
                folder_cursors.append(latest_dt)

        # Fetch from Sent Items (2-way sync)
        if mailbox.x_sync_sent:
            count, latest_dt = self._fetch_folder(mailbox, 'SentItems')
            processed_count += count
            if latest_dt:
                folder_cursors.append(latest_dt)

        # Advance sync cursor incrementally:
        # Use min of folder progress (safe: won't skip messages in slower folder)
        # If no messages found, advance to now() (fully caught up)
        if folder_cursors:
            mailbox.write({'x_last_sync_date': min(folder_cursors)})
        else:
            mailbox.write({'x_last_sync_date': fields.Datetime.now()})

        _logger.info(f"[Incoming Mail] Processed {processed_count} message(s) from {mailbox.email}")

    def _fetch_folder(self, mailbox, folder):
        """
        Fetch messages from a specific folder.

        Messages are sorted ascending (oldest first) so we process
        incrementally. The cursor advances to the last fetched message's
        date, ensuring no messages are skipped across runs.

        Args:
            mailbox: x_microsoft.mailbox record
            folder: Folder name ('Inbox', 'SentItems', etc.)

        Returns:
            tuple: (processed_count, latest_message_datetime or None)
        """
        # Fetch previews since last sync (sorted ascending for incremental cursor)
        previews = mailbox._get_provider()._fetch_message_previews(
            mailbox, folder, since=mailbox.x_last_sync_date, limit=200,
        )

        processed = 0
        latest_datetime = None

        # Previews sorted ascending — last item has the latest date
        if previews:
            latest_datetime = previews[-1]['date']

        for preview in previews:
            try:
                if self._process_message(mailbox, preview, folder):
                    processed += 1
            except Exception:
                _logger.exception(
                    f"[Incoming Mail] Error processing message {preview.get('provider_message_id')}"
                )
                # Continue with next message

        return processed, latest_datetime

    def _process_message(self, mailbox, preview, folder):
        """
        Process a single message using Odoo's native routing.

        Args:
            mailbox: x_microsoft.mailbox record
            preview: message preview (see providers/message.py)
            folder: Folder name

        Returns:
            bool: True if message was processed, False if skipped
        """
        provider = mailbox._get_provider()
        internet_message_id = preview.get('message_id')

        # Check for duplicate — on the preview, before paying for a full fetch
        if self._is_duplicate(internet_message_id):
            _logger.debug(f"[Incoming Mail] Skipping duplicate: {internet_message_id}")
            return False

        _logger.info(f"[Incoming Mail] Processing: {preview.get('subject') or '(no subject)'}")

        # Get full message with headers for threading
        msg = provider._get_message(mailbox, preview['provider_message_id'])
        headers = msg['headers']

        # Check for Odoo-originated emails (skip to prevent import loops)
        # We add X-Odoo-* headers to all outgoing emails
        if headers.get('x-odoo-model') or headers.get('x-odoo-mail-id'):
            _logger.info(f"[Incoming Mail] Skipping Odoo-originated email: {internet_message_id}")
            return False

        # Determine if this is incoming or outgoing (for 2-way sync)
        is_outgoing = folder == 'SentItems'

        # For incoming: use sender (from). For outgoing: use first recipient (to)
        if is_outgoing:
            # Sent Items: get the recipient
            if not msg['to']:
                _logger.info(f"[Incoming Mail] Skipping sent email without recipients: {internet_message_id}")
                return False
            contact_name, contact_email = msg['to'][0]
            _logger.info(f"[Incoming Mail] Sent to: name='{contact_name}', email='{contact_email}'")
        else:
            # Inbox: use the sender
            contact_name, contact_email = msg['from']
            _logger.info(f"[Incoming Mail] From: name='{contact_name}', email='{contact_email}'")

        # Skip emails from/to internal domains (only for incoming, not sent items)
        # This is a per-mailbox setting - team mailboxes may want internal emails logged
        if not is_outgoing and self._is_internal_domain(contact_email, mailbox):
            _logger.info(f"[Incoming Mail] Skipping internal domain: {contact_email}")
            return False

        # Find existing partner (if any)
        partner = self._find_partner(contact_email)

        # Check if partner is blocked
        if partner and partner.x_email_sync_blocked:
            _logger.info(f"[Incoming Mail] Skipping blocked contact: {partner.name} ({contact_email})")
            return False

        # Skip internal users (Odoo employees)
        if partner and partner.user_ids:
            _logger.info(f"[Incoming Mail] Skipping internal user: {partner.name} ({contact_email})")
            return False

        # Determine if this is a known or unknown contact
        is_known_contact = bool(partner)

        # Apply routing rules based on sync mode and contact type
        if mailbox.x_sync_mode == 'known_partners':
            # Only process known contacts
            if not is_known_contact:
                _logger.info(f"[Incoming Mail] Skipping unknown contact (sync mode=known_partners): {contact_email}")
                return False
            _logger.info(f"[Incoming Mail] Known partner filter passed: {partner.name}")

        elif mailbox.x_sync_mode == 'all':
            # 'all' mode: process both known and unknown contacts
            if not is_known_contact:
                # Unknown contact - check routing setting
                if mailbox.x_queue_unknown_contacts:
                    # TODO: Queue for approval - for now, skip
                    _logger.info(f"[Incoming Mail] Unknown contact queued for approval (not implemented yet): {contact_email}")
                    return False
                # 'auto' mode: will create partner below
            _logger.info(f"[Incoming Mail] All contacts mode: processing {'known' if is_known_contact else 'unknown'} contact")

        # Get attachments if present. Deliberately here, after every skip check
        # above: we only pay for attachments of messages we are going to keep.
        # Note: has_attachments is false for inline-only images, so also check
        # for cid: in the body.
        attachments = []
        if msg['has_attachments'] or 'cid:' in msg['body_html']:
            attachments = provider._get_attachments(mailbox, msg['provider_message_id'])
            _logger.info(f"[Incoming Mail] Fetched {len(attachments)} attachment(s)")

        # Find or create the partner (contact) for chatter posting
        partner = None
        if contact_email:
            partner = self._find_or_create_partner(contact_email, contact_name)
            _logger.info(f"[Incoming Mail] Partner resolved: {partner.name} (id={partner.id}, email={partner.email})")

        if not partner:
            _logger.warning(f"[Incoming Mail] Could not resolve partner for {contact_email}, skipping")
            return False

        conversation_id = msg['thread_id']

        # Build email body - mark as safe HTML to preserve formatting
        body_content = msg['body_html']

        # Process attachments into Odoo's expected tuple formats:
        # - Inline (with a cid): 3-tuple so Odoo converts cid: → /web/image/
        # - Regular: 2-tuple stored as ir.attachment
        email_attachments = []
        for attachment in attachments:
            if attachment['is_inline'] and attachment['cid']:
                # Inline: 3-tuple lets Odoo handle cid: conversion
                email_attachments.append(
                    (attachment['name'], attachment['content'], {'cid': attachment['cid']})
                )
            else:
                # Regular: 2-tuple stored as file attachment
                email_attachments.append((attachment['name'], attachment['content']))

        if msg['is_html'] and body_content:
            body_content = Markup(body_content)

        # Build msg_dict in Odoo's expected format for message_new()
        email_from = f'"{contact_name}" <{contact_email}>' if contact_name else contact_email
        cc_addresses = ', '.join(email for _name, email in msg['cc'])
        msg_dict = {
            'message_type': 'email',
            'subject': msg['subject'],
            'from': email_from,
            'to': mailbox.email,
            'cc': cc_addresses,
            'body': body_content,
            'attachments': email_attachments,
            'message_id': internet_message_id,
            'author_id': partner.id,
            'email_from': email_from,
        }

        # Determine correct author for sent items (mailbox owner, not the contact)
        post_author_id = partner.id
        post_email_from = email_from
        if is_outgoing:
            author_name, author_email = msg['from']
            author_email = author_email or mailbox.email
            if mailbox.x_mailbox_type == 'shared':
                author = self._find_or_create_partner(mailbox.email)
            else:
                author = mailbox.x_owner_user_id.partner_id
            post_author_id = author.id
            post_email_from = f'"{author_name}" <{author_email}>' if author_name else author_email

        try:
            # ROUTE TO TEAM: all emails go to CRM/Helpdesk via alias
            if mailbox.x_route_to_team:
                # Find existing team record for this conversation
                team_record_msg = False
                if conversation_id:
                    team_record_msg = self.env['mail.message'].search([
                        ('x_microsoft_conversation_id', '=', conversation_id),
                        ('model', '!=', False),
                        ('model', '!=', 'res.partner'),
                        ('res_id', '!=', False),
                    ], order='id asc', limit=1)

                if team_record_msg:
                    # Thread on existing team record
                    target_record = self.env[team_record_msg.model].browse(team_record_msg.res_id)
                    message = target_record.message_post(
                        body=body_content,
                        subject=msg['subject'],
                        message_type='email',
                        subtype_xmlid='mail.mt_comment',
                        author_id=post_author_id,
                        email_from=post_email_from,
                        message_id=internet_message_id,
                        parent_id=team_record_msg.id,
                        attachments=email_attachments,
                        incoming_email_to=msg_dict.get('to', ''),
                        incoming_email_cc=msg_dict.get('cc', ''),
                    )
                    _logger.info(f"[Incoming Mail] Threaded on {team_record_msg.model}/{team_record_msg.res_id}")
                else:
                    # New conversation → create record via alias
                    target_record, message = self._route_email_via_alias(
                        mailbox=mailbox,
                        partner=partner,
                        msg_dict=msg_dict,
                        contact_email=contact_email,
                    )

            # NO ROUTE TO TEAM: original behavior (partner chatter + threading)
            else:
                # Find parent message for threading
                parent_message = self._find_parent_message(headers, conversation_id)

                if parent_message and parent_message.model and parent_message.res_id:
                    # Reply to existing thread
                    target_record = self.env[parent_message.model].browse(parent_message.res_id)
                    message = target_record.message_post(
                        body=body_content,
                        subject=msg['subject'],
                        message_type='email',
                        subtype_xmlid='mail.mt_comment',
                        author_id=post_author_id,
                        email_from=post_email_from,
                        message_id=internet_message_id,
                        parent_id=parent_message.id,
                        attachments=email_attachments,
                        incoming_email_to=msg_dict.get('to', ''),
                        incoming_email_cc=msg_dict.get('cc', ''),
                    )
                    _logger.info(f"[Incoming Mail] Posted reply to {parent_message.model}/{parent_message.res_id}")
                elif is_outgoing:
                    # Sent item to partner's chatter
                    target_record = partner
                    message = target_record.message_post(
                        body=body_content,
                        subject=msg['subject'],
                        message_type='email',
                        subtype_xmlid='mail.mt_comment',
                        author_id=post_author_id,
                        email_from=post_email_from,
                        message_id=internet_message_id,
                        attachments=email_attachments,
                    )
                    _logger.info(f"[Incoming Mail] Posted sent item to partner {partner.name}")
                else:
                    # New incoming email → route via alias or partner chatter
                    target_record, message = self._route_email_via_alias(
                        mailbox=mailbox,
                        partner=partner,
                        msg_dict=msg_dict,
                        contact_email=contact_email,
                    )

            # Store Microsoft conversationId for threading future replies
            if conversation_id and message:
                message.write({'x_microsoft_conversation_id': conversation_id})

            _logger.info(f"[Incoming Mail] Successfully processed: {internet_message_id} -> {target_record._name}/{target_record.id}")
            return True

        except Exception:
            _logger.exception(f"[Incoming Mail] Failed to process message: {internet_message_id}")
            raise

    def _find_parent_message(self, headers, conversation_id):
        """Find parent message for threading via In-Reply-To or conversationId."""
        parent_message = False
        in_reply_to = headers.get('in-reply-to')

        if in_reply_to:
            parent_message = self.env['mail.message'].search([
                ('x_microsoft_message_id', '=', in_reply_to)
            ], limit=1)
            if not parent_message:
                parent_message = self.env['mail.message'].search([
                    ('message_id', '=', in_reply_to)
                ], limit=1)

        if not parent_message and conversation_id:
            parent_message = self.env['mail.message'].search([
                ('x_microsoft_conversation_id', '=', conversation_id),
                ('model', '!=', False),
                ('res_id', '!=', False),
            ], order='id asc', limit=1)

        return parent_message

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

    def _is_internal_domain(self, email, mailbox=None):
        """
        Check if email is from an internal company domain.

        Uses Odoo's standard mail.alias.domain to determine internal domains.
        The per-mailbox 'Exclude Internal Emails' setting controls whether
        filtering is applied. Team mailboxes may disable this to log
        internal email forwarding.

        Args:
            email: Email address to check
            mailbox: x_microsoft.mailbox record (optional, for per-mailbox setting)

        Returns:
            bool: True if email should be skipped as internal
        """
        # Check per-mailbox setting first
        if mailbox and not mailbox.x_exclude_internal:
            return False  # Don't exclude internal emails for this mailbox

        if not email or '@' not in email:
            return False

        sender_domain = email.lower().split('@')[1]

        # Get all configured alias domains from Odoo (standard mail.alias.domain)
        alias_domains = self.env['mail.alias.domain'].sudo().search([])
        if not alias_domains:
            return False

        internal_domains = [d.name.lower() for d in alias_domains if d.name]

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

    def _route_email_via_alias(self, mailbox, partner, msg_dict, contact_email):
        """
        Route incoming email using Odoo's native message_new() method.

        This leverages Odoo's built-in mail handling which:
        - Creates the record (ticket, lead, etc.)
        - Posts the initial message
        - Triggers auto-replies if configured (e.g., Helpdesk acknowledgment)
        - Does NOT send duplicate notifications to the sender

        Args:
            mailbox: x_microsoft.mailbox record with routing configuration
            partner: res.partner record for the sender
            msg_dict: Parsed email dict in Odoo format
            contact_email: Sender email address

        Returns:
            tuple: (record, message) - the created record and its first message
        """
        import ast

        # Check if routing to team is enabled
        route_to_team = mailbox.x_route_to_team if mailbox else False
        alias = mailbox.x_alias_id if mailbox and route_to_team else False

        # Route to Contact: post to partner's chatter (default behavior)
        if not route_to_team or not alias or not alias.alias_model_id:
            _logger.info(f"[Incoming Mail] Routing to contact chatter for mailbox {mailbox.email}")
            message = partner.message_post(
                body=msg_dict.get('body', ''),
                subject=msg_dict.get('subject', ''),
                message_type='email',
                subtype_xmlid='mail.mt_comment',
                author_id=partner.id,
                email_from=msg_dict.get('email_from'),
                message_id=msg_dict.get('message_id'),
                attachments=msg_dict.get('attachments', []),
                incoming_email_to=msg_dict.get('to', ''),
                incoming_email_cc=msg_dict.get('cc', ''),
            )
            return partner, message

        model = alias.alias_model_id.model
        _logger.info(f"[Incoming Mail] Routing via alias '{alias.display_name}' -> {model}")

        # Parse alias_defaults for team_id, user_id, etc.
        custom_values = {}
        if alias.alias_defaults:
            try:
                custom_values = ast.literal_eval(alias.alias_defaults)
            except (ValueError, SyntaxError):
                pass

        # Create the record via message_new() with context flags matching
        # Odoo's standard _message_route_process() behavior:
        # - mail_create_nosubscribe: don't auto-subscribe the sender as follower
        # - mail_create_nolog: don't post a "Record created" log message
        Model = self.env[model].with_context(
            mail_create_nosubscribe=True,
            mail_create_nolog=True,
        )
        record = Model.message_new(msg_dict, custom_values=custom_values)

        # Post the email body to the chatter (message_new only creates the record).
        # incoming_email_to/cc prevent Odoo from sending notifications back to
        # recipients who were already on the original email (including the sender).
        message = record.message_post(
            body=msg_dict.get('body', ''),
            subject=msg_dict.get('subject', ''),
            message_type='email',
            subtype_xmlid='mail.mt_comment',
            author_id=msg_dict.get('author_id'),
            email_from=msg_dict.get('email_from'),
            message_id=msg_dict.get('message_id'),
            attachments=msg_dict.get('attachments', []),
            incoming_email_to=msg_dict.get('to', ''),
            incoming_email_cc=msg_dict.get('cc', ''),
        )

        _logger.info(f"[Incoming Mail] Created {model} via message_new: {record.display_name} (id={record.id})")
        return record, message
