# -*- coding: utf-8 -*-
import logging
from odoo import fields, models, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MailMail(models.Model):
    """Extend mail.mail to support Microsoft Graph API sending"""
    _inherit = 'mail.mail'

    x_microsoft_mailbox_id = fields.Many2one(
        'x_microsoft.mailbox',
        string='Send From Mailbox',
        help='Microsoft mailbox to send this email from'
    )

    x_microsoft_message_id = fields.Char(
        string='Microsoft Message ID',
        help='Microsoft internetMessageId - used to prevent duplicate imports from Sent Items',
        index=True,
    )

    x_microsoft_conversation_id = fields.Char(
        string='Microsoft Conversation ID',
        help='Microsoft conversationId - used for email threading',
        index=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        """Set mailbox from context if provided by mail.compose.message."""
        mailbox_id = self.env.context.get('microsoft_mailbox_id')
        if mailbox_id:
            for vals in vals_list:
                if not vals.get('x_microsoft_mailbox_id'):
                    vals['x_microsoft_mailbox_id'] = mailbox_id
        return super().create(vals_list)

    def _is_outlook_pro_configured(self):
        """
        Check if Outlook Pro module is minimally configured.

        Returns True if at least one active mailbox exists.
        This allows the system to work before setup is complete.
        """
        return bool(self.env['x_microsoft.mailbox'].sudo().search_count([('active', '=', True)]))

    def send(self, auto_commit=False, raise_exception=False, post_send_callback=None):
        """
        Override send() to route emails through Microsoft Graph API.

        Mass mailing emails (Email Marketing campaigns) are excluded and sent
        via standard SMTP (e.g. Brevo), since they use mailing.mailing infrastructure.
        Marketing Automation emails use message_post() and don't set mailing_id,
        so they correctly route through Graph API.

        Args:
            auto_commit: Whether to commit after each email (ignored, we handle our own state)
            raise_exception: Whether to raise exceptions or just log them
            post_send_callback: Odoo 19 callback function called after successful send
        """
        # Mass mailing emails → standard SMTP (e.g. Brevo).
        # `mailing_id` only exists when the mass_mailing module is installed;
        # treat it as "no mass mails" otherwise so this module works standalone.
        if 'mailing_id' in self._fields:
            mass_mails = self.filtered(lambda m: m.mailing_id)
        else:
            mass_mails = self.env['mail.mail']
        if mass_mails:
            _logger.info(f"[Graph API] Routing {len(mass_mails)} mass mailing email(s) via standard SMTP")
            super(MailMail, mass_mails).send(
                auto_commit=auto_commit,
                raise_exception=raise_exception,
                post_send_callback=post_send_callback,
            )

        graph_mails = self - mass_mails
        if not graph_mails:
            return True

        # If no mailboxes configured yet, skip email silently (don't block user actions)
        if not graph_mails._is_outlook_pro_configured():
            _logger.warning("[Graph API] No mailboxes configured - emails will not be sent until Outlook Pro is set up")
            for mail in graph_mails:
                mail.write({'state': 'cancel'})
            return True

        _logger.info(f"[Graph API] send() called for {len(graph_mails)} email(s)")

        for mail in graph_mails:
            try:
                success, error_msg = mail._send_via_microsoft_graph()
                if success:
                    # Call post_send_callback if provided (Odoo 19 feature)
                    if post_send_callback:
                        post_send_callback(mail)
                else:
                    # Always raise configuration errors so user sees them
                    raise UserError(error_msg or _('Failed to send email via Microsoft Graph API'))
            except UserError:
                # Re-raise UserErrors (configuration problems) so user sees them
                raise
            except Exception as e:
                _logger.exception(f"[Graph API] Exception sending email {mail.id}")
                mail.write({
                    'state': 'exception',
                    'failure_reason': str(e),
                })
                if raise_exception:
                    raise
                # For non-config errors, show a generic message
                raise UserError(_('Failed to send email: %s') % str(e))

        return True

    def _send_via_microsoft_graph(self):
        """
        Send this email via Microsoft Graph API.

        Returns:
            tuple: (success: bool, error_msg: str or None)
        """
        self.ensure_one()

        _logger.info(f"[Graph API] Processing email {self.id}: subject='{self.subject}', to={self.email_to}")

        # Determine mailbox and user based on email type
        mailbox, user = self._get_mailbox_and_user()

        if not mailbox:
            error_msg = self._get_missing_mailbox_error()
            _logger.error(f"[Graph API] {error_msg}")
            self.write({
                'state': 'exception',
                'failure_reason': error_msg,
            })
            return (False, error_msg)

        if not user or not user.x_microsoft_access_token:
            error_msg = self._get_missing_user_error(user)
            _logger.error(f"[Graph API] {error_msg}")
            self.write({
                'state': 'exception',
                'failure_reason': error_msg,
            })
            return (False, error_msg)

        _logger.info(f"[Graph API] Sending email {self.id} from mailbox {mailbox.email}")

        # Get Graph API client
        graph_client = self.env['microsoft.graph.client']

        # Send email using user's delegated token (principle of least privilege)
        result = graph_client.send_email_via_graph(
            mail_record=self,
            mailbox=mailbox,
            user=user,
        )

        if result['success']:
            # Mark as sent and store Microsoft IDs for duplicate detection and threading
            microsoft_message_id = result.get('microsoft_message_id')
            microsoft_conversation_id = result.get('microsoft_conversation_id')

            self.write({
                'state': 'sent',
                'x_microsoft_message_id': microsoft_message_id,
                'x_microsoft_conversation_id': microsoft_conversation_id,
            })

            # Also update mail_message for threading to work
            # When reply comes in with In-Reply-To header containing the Microsoft ID,
            # we can find this message via x_microsoft_message_id
            if self.mail_message_id and microsoft_message_id:
                self.mail_message_id.write({
                    'x_microsoft_message_id': microsoft_message_id,
                    'x_microsoft_conversation_id': microsoft_conversation_id,
                })
                _logger.info(f"[Graph API] Updated mail.message {self.mail_message_id.id} with Microsoft IDs for threading")

            _logger.info(f"[Graph API] Email {self.id} sent successfully from {mailbox.email}")
            _logger.info(f"[Graph API] Stored Microsoft IDs - Message: {microsoft_message_id}, Conversation: {microsoft_conversation_id}")
            return (True, None)
        else:
            # Mark as exception
            error_msg = result.get('error', 'Unknown error')
            self.write({
                'state': 'exception',
                'failure_reason': error_msg,
            })
            _logger.error(f"[Graph API] Email {self.id} failed to send: {error_msg}")
            return (False, error_msg)

    def _is_internal_user_notification(self):
        """
        Check if this mail is a notification to an internal Odoo user.

        Logic: If a mail.mail exists for a partner that is linked to a res.users,
        it means _notify_thread_by_email() was called for that user. This only
        happens when the user has notification_type='email' in their preferences.

        Users with notification_type='inbox' never get a mail.mail created for them
        (they get inbox notifications instead).

        Therefore: any mail.mail going to a user-linked partner = internal notification
        → should use notifications@ mailbox.

        Returns:
            bool: True if any recipient is an internal Odoo user
        """
        self.ensure_one()
        # Check recipient_ids - if any partner is linked to a user, it's internal
        for partner in self.recipient_ids:
            if partner.user_ids:
                _logger.info(f"[Graph API] Email {self.id} IS internal user notification to {partner.name}")
                return True
        return False

    def _get_mailbox_and_user(self):
        """
        Determine which mailbox and user to use for sending.

        Resolution order:
        1. Internal user notification → notification mailbox
        2. Explicit composer "Send From" dropdown selection → that mailbox
        3. Author-based default → author's default mailbox

        For personal/shared mailboxes: sender uses their own OAuth token.
        For notification mailboxes: uses the owner's OAuth token.

        Returns:
            tuple: (mailbox, user) or (None, None) if not configured
        """
        self.ensure_one()

        # 1. For notifications to internal users, use notification mailbox
        if self._is_internal_user_notification():
            return self._get_notification_mailbox_and_user()

        # 2. Honor explicit "Send From" selection from the composer.
        #    The dropdown is a stronger signal than author_id heuristics, which
        #    misfire when a template's email_from matches the company partner
        #    (e.g. sale order quotations) instead of the actual sender.
        if self.x_microsoft_mailbox_id:
            mailbox = self.x_microsoft_mailbox_id
            sender = self._resolve_sender_for_selected_mailbox(mailbox)
            if sender and sender.x_microsoft_oauth_connected:
                _logger.info(
                    f"[Graph API] Using explicitly selected mailbox: {mailbox.email} (sender: {sender.name})"
                )
                return (mailbox, sender)
            _logger.warning(
                f"[Graph API] Selected mailbox {mailbox.email} has no OAuth-connected sender; "
                f"falling back to author/notification routing"
            )

        # 3. For regular emails, author MUST be a user with OAuth configured
        if not self.author_id:
            _logger.error(f"[Graph API] Email {self.id} has no author_id set")
            return (None, None)

        # Author must be linked to exactly one Odoo user
        # Exception: if author is external (no user), use notification mailbox
        # This handles emails triggered by incoming mail (e.g., auto-replies, activity notifications)
        if not self.author_id.user_ids:
            _logger.info(f"[Graph API] Author {self.author_id.name} is external, using notification mailbox")
            return self._get_notification_mailbox_and_user()

        author_user = self.author_id.user_ids[0]

        if not author_user.x_microsoft_default_mailbox_id:
            _logger.info(f"[Graph API] User {author_user.name} has no default mailbox, falling back to notification mailbox")
            return self._get_notification_mailbox_and_user()

        mailbox = author_user.x_microsoft_default_mailbox_id

        # Both personal and shared mailboxes: author sends with their own token
        # For shared mailboxes, user needs Mail.Send.Shared permission + SendAs rights in M365
        if not author_user.x_microsoft_oauth_connected:
            _logger.info(f"[Graph API] User {author_user.name} has no Microsoft connection, falling back to notification mailbox")
            return self._get_notification_mailbox_and_user()
        return (mailbox, author_user)

    def _resolve_sender_for_selected_mailbox(self, mailbox):
        """Pick the OAuth-connected user whose token should send from `mailbox`.

        - notification/personal: owner is the only viable token holder
        - shared: prefer the author's user (correct in cron context where
          env.user is the cron runner), then fall back to env.user
        """
        self.ensure_one()
        if mailbox.x_mailbox_type in ('notification', 'personal'):
            return mailbox.x_owner_user_id
        # Shared mailbox: anyone with SendAs rights can send with their own token.
        if self.author_id and self.author_id.user_ids:
            return self.author_id.user_ids[0]
        env_user = self.env.user
        if env_user and not env_user._is_public():
            return env_user
        return self.env['res.users']

    def _get_notification_mailbox_and_user(self):
        """
        Get the notification mailbox (type='notification') and its owner.

        Returns:
            tuple: (mailbox, user) or (None, None) if not configured
        """
        # Find the notification mailbox by type
        mailbox = self.env['x_microsoft.mailbox'].search([
            ('x_mailbox_type', '=', 'notification'),
            ('active', '=', True),
        ], limit=1)

        if not mailbox:
            _logger.error("[Graph API] No notification mailbox configured")
            return (None, None)

        owner = mailbox.get_sending_user()  # Returns owner for notification mailboxes
        if not owner:
            _logger.error(f"[Graph API] Notification mailbox {mailbox.email} has no owner configured")
            return (None, None)

        if not owner.x_microsoft_oauth_connected:
            _logger.error(f"[Graph API] Notification mailbox owner {owner.name} has no Microsoft account connected")
            return (None, None)

        return (mailbox, owner)

    def _get_missing_mailbox_error(self):
        """Generate appropriate error message for missing mailbox configuration."""
        self.ensure_one()

        if self._is_internal_user_notification():
            # Check if notification mailbox exists
            mailbox = self.env['x_microsoft.mailbox'].search([
                ('x_mailbox_type', '=', 'notification'),
                ('active', '=', True),
            ], limit=1)

            if not mailbox:
                return _(
                    'No Notification mailbox configured. '
                    'Go to Settings → Outlook Pro → Manage Mailbox List and create a mailbox with type "Notification".'
                )

            if not mailbox.x_owner_user_id:
                return _(
                    'Notification mailbox "%s" has no Owner configured. '
                    'Edit the mailbox and select a user with Microsoft OAuth connected.'
                ) % mailbox.email

            if not mailbox.x_owner_user_id.x_microsoft_oauth_connected:
                return _(
                    'Notification mailbox owner "%s" has no Microsoft account connected. '
                    'The user must connect their Microsoft account first.'
                ) % mailbox.x_owner_user_id.name

            return _('Unknown notification mailbox configuration error.')

        # Check specific failure reason for regular emails
        if not self.author_id:
            return _('Email has no author. Cannot determine which mailbox to use.')

        if not self.author_id.user_ids:
            return _(
                'Author "%s" is not linked to an Odoo user. '
                'Emails can only be sent by Odoo users with Microsoft OAuth configured.'
            ) % self.author_id.name

        user = self.author_id.user_ids[0]

        if not user.x_microsoft_default_mailbox_id:
            return _(
                'User "%s" has no default mailbox configured. '
                'Go to My Profile → Email and select a Default Send From mailbox.'
            ) % user.name

        mailbox = user.x_microsoft_default_mailbox_id

        # Both personal and shared mailboxes require user to have OAuth connected
        if not user.x_microsoft_oauth_connected:
            if mailbox.x_mailbox_type == 'shared':
                return _(
                    'User "%s" has no Microsoft account connected. '
                    'To send from shared mailbox "%s", connect your Microsoft account and ensure you have SendAs permission.'
                ) % (user.name, mailbox.email)
            else:
                return _(
                    'User "%s" has no Microsoft account connected. '
                    'Go to My Profile → Outlook Pro and click "Connect Microsoft 365 account".'
                ) % user.name

        return _('Unknown mailbox configuration error.')

    def _get_missing_user_error(self, user):
        """Generate appropriate error message for missing user OAuth."""
        self.ensure_one()

        if self._is_internal_user_notification():
            return _(
                'Notification sender not configured or not connected to Microsoft. '
                'Go to Settings → Outlook Pro and configure the Notification Sender.'
            )

        if not user:
            return _('No user found to send email. Author must be linked to an Odoo user.')

        if not user.x_microsoft_access_token:
            return _(
                'User "%s" has no valid Microsoft access token. '
                'Please reconnect Microsoft account in My Profile → Email.'
            ) % user.name

        return _('Unknown user configuration error.')
