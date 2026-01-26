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

    # Removed create() override - mailbox is determined at send time based on author
    # This prevents issues where env.user differs from the actual email author

    def send(self, auto_commit=False, raise_exception=False, post_send_callback=None):
        """
        Override send() to route ALL emails through Microsoft Graph API.

        No SMTP fallback - all emails must go via Graph API.
        Configuration errors ALWAYS raise UserError so user sees the problem.

        Args:
            auto_commit: Whether to commit after each email (ignored, we handle our own state)
            raise_exception: Whether to raise exceptions or just log them
            post_send_callback: Odoo 19 callback function called after successful send
        """
        _logger.info(f"[Graph API] send() called for {len(self)} email(s)")

        for mail in self:
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

        # Send email - pass mailbox record so it can use UPN if configured
        result = graph_client.send_email_via_graph(
            mail_record=self,
            mailbox=mailbox,
        )

        if result['success']:
            # Mark as sent
            self.write({
                'state': 'sent',
                'message_id': result.get('message_id', self.message_id),
            })
            _logger.info(f"Email {self.id} sent successfully via Graph API from {mailbox.email}")
            return (True, None)
        else:
            # Mark as exception
            error_msg = result.get('error', 'Unknown error')
            self.write({
                'state': 'exception',
                'failure_reason': error_msg,
            })
            _logger.error(f"Email {self.id} failed to send via Graph API: {error_msg}")
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

        NO FALLBACKS - explicit configuration required:
        1. Internal user notification → system notification mailbox (from settings)
        2. Regular email → author must have OAuth + default mailbox configured

        Returns:
            tuple: (mailbox, user) or (None, None) if not configured
        """
        self.ensure_one()

        # 1. For notifications to internal users, use system notification mailbox
        if self._is_internal_user_notification():
            return self._get_notification_mailbox_and_user()

        # 2. For regular emails, author MUST be a user with OAuth configured
        if not self.author_id:
            _logger.error(f"[Graph API] Email {self.id} has no author_id set")
            return (None, None)

        # Author must be linked to exactly one Odoo user
        if not self.author_id.user_ids:
            _logger.error(f"[Graph API] Author {self.author_id.name} is not linked to any Odoo user")
            return (None, None)

        user = self.author_id.user_ids[0]

        # User must have OAuth connected
        if not user.x_microsoft_oauth_connected:
            _logger.error(f"[Graph API] User {user.name} has no Microsoft account connected")
            return (None, None)

        # User must have a default mailbox
        if not user.x_microsoft_default_mailbox_id:
            _logger.error(f"[Graph API] User {user.name} has no default mailbox configured")
            return (None, None)

        return (user.x_microsoft_default_mailbox_id, user)

    def _get_notification_mailbox_and_user(self):
        """
        Get the system notification mailbox and user from settings.

        Returns:
            tuple: (mailbox, user) or (None, None) if not configured
        """
        IrConfigParameter = self.env['ir.config_parameter'].sudo()

        mailbox_id = IrConfigParameter.get_param('x_pan_outlook_pro.notification_mailbox_id')
        user_id = IrConfigParameter.get_param('x_pan_outlook_pro.notification_user_id')

        if not mailbox_id or not user_id:
            return (None, None)

        mailbox = self.env['x_microsoft.mailbox'].browse(int(mailbox_id)).exists()
        user = self.env['res.users'].browse(int(user_id)).exists()

        if not mailbox or not user:
            return (None, None)

        return (mailbox, user)

    def _get_missing_mailbox_error(self):
        """Generate appropriate error message for missing mailbox configuration."""
        self.ensure_one()

        if self._is_internal_user_notification():
            return _(
                'System notification mailbox not configured. '
                'Go to Settings → Outlook Pro and configure the Notification Mailbox.'
            )

        # Check specific failure reason
        if not self.author_id:
            return _('Email has no author. Cannot determine which mailbox to use.')

        if not self.author_id.user_ids:
            return _(
                'Author "%s" is not linked to an Odoo user. '
                'Emails can only be sent by Odoo users with Microsoft OAuth configured.'
            ) % self.author_id.name

        user = self.author_id.user_ids[0]

        if not user.x_microsoft_oauth_connected:
            return _(
                'User "%s" has no Microsoft account connected. '
                'Go to My Profile → Email and click "Connect Microsoft Account".'
            ) % user.name

        if not user.x_microsoft_default_mailbox_id:
            return _(
                'User "%s" has no default mailbox configured. '
                'Go to My Profile → Email and select a Default Send From mailbox.'
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
