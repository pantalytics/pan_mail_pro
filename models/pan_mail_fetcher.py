# -*- coding: utf-8 -*-
"""
Incoming Mail Processor.

Fetches emails from a mailbox via its provider client and routes them to the
correct partner, using message_new()/message_post() for proper threading.

Provider-neutral: everything here reads the normalized message shape documented
in `mail_provider_client.py`. No Graph, Gmail or other wire-specific key should
ever appear below this line.
"""
import logging
from markupsafe import Markup

from odoo import models, api, fields

from .mail_provider_client import FOLDER_INBOX, FOLDER_SENT

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
                with self.env.cr.savepoint():
                    self._process_mailbox(mailbox)
                    # Mark as active if successful
                    if mailbox.state != 'active':
                        mailbox.write({'state': 'active', 'x_error_message': False})
            except Exception as e:
                # Savepoint rolled back: the cursor is usable again, so the
                # error write below won't hit "current transaction is aborted".
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
                client = mailbox._get_client()
                client.fetch_messages(
                    account=client.resolve_receiving_account(mailbox),
                    mailbox=mailbox,
                    folder=FOLDER_INBOX,
                    limit=1,  # Just test, don't fetch all
                )
                _logger.info(f"[Incoming Mail] Connection test passed for {mailbox.email}, setting sync date to now")
                mailbox.write({'x_last_sync_date': fields.Datetime.now()})
                return  # Skip this run, start fetching from next cron run

        processed_count = 0
        folder_cursors = []

        # Fetch from Inbox
        if mailbox.x_sync_inbox:
            count, latest_dt = self._fetch_folder(mailbox, FOLDER_INBOX)
            processed_count += count
            if latest_dt:
                folder_cursors.append(latest_dt)

        # Fetch from Sent Items (2-way sync)
        if mailbox.x_sync_sent:
            count, latest_dt = self._fetch_folder(mailbox, FOLDER_SENT)
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
            mailbox: mailbox record
            folder: FOLDER_INBOX or FOLDER_SENT

        Returns:
            tuple: (processed_count, latest_received_datetime or None)
        """
        # Fetch messages since last sync (sorted ascending for incremental cursor)
        client = mailbox._get_client()
        messages = client.fetch_messages(
            account=client.resolve_receiving_account(mailbox),
            mailbox=mailbox,
            folder=folder,
            since_datetime=mailbox.x_last_sync_date,
            limit=200,
        )

        processed = 0
        latest_datetime = None

        # Messages sorted ascending — the last item carries the latest date
        if messages:
            latest_datetime = messages[-1].get('date')

        for message in messages:
            try:
                with self.env.cr.savepoint():
                    if self._process_message(mailbox, message, folder):
                        processed += 1
            except Exception:
                # Without the savepoint, one DB error would leave the whole
                # transaction in `aborted` state and every later message in
                # this batch would fail with "cursor already closed".
                _logger.exception(
                    f"[Incoming Mail] Error processing message {message.get('provider_message_id')}"
                )

        return processed, latest_datetime

    def _process_message(self, mailbox, message, folder):
        """
        Process a single message using Odoo's native routing.

        Args:
            mailbox: mailbox record
            message: normalized message dict from the provider client (preview)
            folder: FOLDER_INBOX or FOLDER_SENT

        Returns:
            bool: True if message was processed, False if skipped
        """
        internet_message_id = message.get('message_id')
        # Captured before `message` is rebound below to the posted mail.message.
        # This is the provider's own resource handle, not the RFC Message-ID.
        provider_message_id = message.get('provider_message_id')

        # Check for duplicate
        if self._is_duplicate(internet_message_id):
            _logger.debug(f"[Incoming Mail] Skipping duplicate: {internet_message_id}")
            return False

        _logger.info(f"[Incoming Mail] Processing: {message.get('subject') or '(no subject)'}")

        # Get full message with headers for threading
        client = mailbox._get_client()
        account = client.resolve_receiving_account(mailbox)
        full_message = client.get_message(
            account=account,
            mailbox=mailbox,
            provider_message_id=message['provider_message_id'],
        )

        # Check for Odoo-originated emails (skip to prevent import loops)
        # We add X-Odoo-* headers to all outgoing emails
        headers = full_message.get('headers', {})
        if headers.get('x-odoo-model') or headers.get('x-odoo-mail-id'):
            _logger.info(f"[Incoming Mail] Skipping Odoo-originated email: {internet_message_id}")
            return False

        # Determine if this is incoming or outgoing (for 2-way sync)
        is_outgoing = folder == FOLDER_SENT

        # For incoming: use sender (from). For outgoing: use first recipient (to)
        if is_outgoing:
            # Sent Items: get the recipient
            to_recipients = full_message.get('to') or []
            if not to_recipients:
                _logger.info(f"[Incoming Mail] Skipping sent email without recipients: {internet_message_id}")
                return False
            contact_email = to_recipients[0].get('email', '')
            contact_name = to_recipients[0].get('name', '')
            _logger.info(f"[Incoming Mail] Sent to: name='{contact_name}', email='{contact_email}'")
        else:
            # Inbox: use the sender
            sender = full_message.get('from') or {}
            contact_email = sender.get('email', '')
            contact_name = sender.get('name', '')
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

        # Get attachments if present
        # Note: has_attachments is false for inline-only images, so also check for cid: in body
        attachments = []
        body_may_have_inline = 'cid:' in (full_message.get('body_html') or '')
        if full_message.get('has_attachments') or body_may_have_inline:
            attachments = client.get_message_attachments(
                account=account,
                mailbox=mailbox,
                provider_message_id=message['provider_message_id'],
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

        # Where does this mail belong? The fetcher decides whether a message is
        # worth keeping; deciding where it goes is the matcher's job, and it is
        # provider-neutral — the same ladder serves Graph, Gmail and IMAP.
        match = self.env['pan.mail.matcher'].match(
            full_message,
            mailbox=mailbox,
            partner=partner,
            # In team mode a reply must land on the ticket or lead, never back
            # on the contact's own chatter.
            exclude_models=('res.partner',) if mailbox.x_route_to_team else (),
        )
        # Effective thread id: what the provider said, or — for providers with
        # no thread concept — the root of the References chain.
        conversation_id = match['thread_id']

        # Build email body - mark as safe HTML to preserve formatting
        body_content = full_message.get('body_html') or ''

        # Process attachments into Odoo's expected tuple format:
        # - Inline: 3-tuple so Odoo converts cid: → /web/image/
        # - Regular: 2-tuple stored as ir.attachment
        email_attachments = []
        for attachment in attachments:
            if attachment['is_inline'] and attachment['content_id']:
                email_attachments.append((
                    attachment['name'],
                    attachment['content'],
                    {'cid': attachment['content_id']},
                ))
            else:
                email_attachments.append((attachment['name'], attachment['content']))

        if full_message.get('body_is_html') and body_content:
            body_content = Markup(body_content)

        # Build msg_dict in Odoo's expected format for message_new()
        email_from = f'"{contact_name}" <{contact_email}>' if contact_name else contact_email
        cc_addresses = ', '.join(
            r.get('email', '') for r in full_message.get('cc') or []
        )
        msg_dict = {
            'message_type': 'email',
            'subject': full_message.get('subject', ''),
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
            sender = full_message.get('from') or {}
            author_email = sender.get('email') or mailbox.email
            author_name = sender.get('name', '')
            if mailbox.x_mailbox_type == 'shared':
                author = self._find_or_create_partner(mailbox.email)
            else:
                author = mailbox.x_owner_user_id.partner_id
            post_author_id = author.id
            post_email_from = f'"{author_name}" <{author_email}>' if author_name else author_email

        try:
            if match['model']:
                # The matcher placed it. Both routing modes take this path —
                # the only difference between them is which models the matcher
                # was allowed to consider, which was decided above.
                target_record = self.env[match['model']].browse(match['res_id'])
                message = target_record.message_post(
                    body=body_content,
                    subject=full_message.get('subject', ''),
                    message_type='email',
                    subtype_xmlid='mail.mt_comment',
                    author_id=post_author_id,
                    email_from=post_email_from,
                    message_id=internet_message_id,
                    parent_id=match['parent_message_id'],
                    attachments=email_attachments,
                    incoming_email_to=msg_dict.get('to', ''),
                    incoming_email_cc=msg_dict.get('cc', ''),
                )
                _logger.info(
                    f"[Incoming Mail] Threaded onto {match['model']}/{match['res_id']} "
                    f"by rule '{match['rule']}'"
                )
                outcome = 'threaded'
            elif is_outgoing and not mailbox.x_route_to_team:
                # Sent item we could not thread: the correspondent's chatter is
                # the only sensible home for it.
                outcome = 'sent_item'
                target_record = partner
                message = target_record.message_post(
                    body=body_content,
                    subject=full_message.get('subject', ''),
                    message_type='email',
                    subtype_xmlid='mail.mt_comment',
                    author_id=post_author_id,
                    email_from=post_email_from,
                    message_id=internet_message_id,
                    attachments=email_attachments,
                )
                _logger.info(f"[Incoming Mail] Posted sent item to partner {partner.name}")
            else:
                # Nothing to thread onto → new record via alias, or the
                # contact's chatter when no alias is configured.
                target_record, message = self._route_email_via_alias(
                    mailbox=mailbox,
                    partner=partner,
                    msg_dict=msg_dict,
                    contact_email=contact_email,
                )
                # Landing on the sender's own chatter means no alias was
                # configured or none applied — delivered, but nobody is looking
                # there. Worth telling apart from a record we deliberately
                # created.
                outcome = 'fallback' if target_record == partner else 'created'

            self.env['pan.mail.routing.log'].log_decision(
                mailbox=mailbox,
                match=match,
                outcome=outcome,
                message=message,
                target_record=target_record,
                subject=full_message.get('subject'),
                email_from=email_from,
                internet_message_id=internet_message_id,
            )

            self._index_message(
                mailbox=mailbox,
                message=message,
                target_record=target_record,
                internet_message_id=internet_message_id,
                conversation_id=conversation_id,
                provider_message_id=provider_message_id,
            )

            _logger.info(f"[Incoming Mail] Successfully processed: {internet_message_id} -> {target_record._name}/{target_record.id}")
            return True

        except Exception:
            _logger.exception(f"[Incoming Mail] Failed to process message: {internet_message_id}")
            raise

    def _index_message(self, mailbox, message, target_record, internet_message_id,
                       conversation_id, provider_message_id=None):
        """Record what we just learned, so the next reply in this thread matches.

        Three writes, each for a different rung of the matching ladder:

        - the Message-ID under which this mail can be referenced, but only when
          it differs from what `message_post` already stored on `mail.message`.
          On import those are normally identical, so this usually writes nothing.
        - the (mailbox, thread id) → record link, which is the scoped lookup.
        - `x_microsoft_conversation_id`, kept as a denormalised copy for the
          legacy lookup and for databases mid-upgrade.
        """
        if not message:
            return

        if internet_message_id and message.message_id != internet_message_id:
            self.env['pan.mail.message.ref'].record(
                message, internet_message_id, source='provider')

        if conversation_id:
            message.write({'x_microsoft_conversation_id': conversation_id})
            if target_record:
                self.env['pan.mail.thread.link'].record(
                    mailbox=mailbox,
                    thread_id=conversation_id,
                    model=target_record._name,
                    res_id=target_record.id,
                    message=message,
                    provider_message_id=provider_message_id,
                )

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
