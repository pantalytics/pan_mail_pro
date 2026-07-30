# -*- coding: utf-8 -*-
import logging
from odoo import fields, models, api, _
from odoo.exceptions import AccessError, UserError

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
        for vals in vals_list:
            self._check_mailbox_permission(vals.get('x_microsoft_mailbox_id'))
        return super().create(vals_list)

    def write(self, vals):
        """Guard the sender mailbox on write as well as on create."""
        if 'x_microsoft_mailbox_id' in vals:
            self._check_mailbox_permission(vals['x_microsoft_mailbox_id'])
        return super().write(vals)

    @api.model
    def _check_mailbox_permission(self, mailbox_id):
        """Refuse a sender mailbox the requesting user is not entitled to.

        Creation is the right place for this check: here `env.user` is still the
        real user. At send time the queue runs in cron, where `env.user` is the
        cron runner and the question can no longer be answered.

        Superuser is exempt — system mail, templates and the notification
        routing in `_get_mailbox_and_account()` legitimately pick a mailbox on
        nobody's behalf.
        """
        if not mailbox_id or self.env.su:
            return
        mailbox = self.env['x_microsoft.mailbox'].sudo().browse(mailbox_id)
        if not mailbox.exists() or mailbox._is_sendable_by(self.env.user):
            return
        _logger.warning(
            "[Graph API] User %s (id=%s) tried to send from mailbox %s (type=%s, owner=%s)",
            self.env.user.login, self.env.user.id, mailbox.email,
            mailbox.x_mailbox_type, mailbox.x_owner_user_id.login or '-',
        )
        raise AccessError(_(
            "You are not allowed to send email from %(mailbox)s. "
            "Personal mailboxes can only be used by their owner.",
            mailbox=mailbox.email,
        ))

    def _is_mail_pro_configured(self):
        """
        Check if Mail Pro module is minimally configured.

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
        # use hasattr per-record so this module also works standalone.
        mass_mails = self.filtered(lambda m: hasattr(m, 'mailing_id') and m.mailing_id)
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

        # If Mail Pro is not in use yet (no mailboxes anywhere in the system,
        # including archived ones), fall through to Odoo's standard mail handling.
        # This keeps demo/QA/dev environments working out-of-the-box: standard SMTP
        # (or the mail queue) takes over until an admin actually configures Graph
        # routing. `active_test=False` is essential — once an admin has created a
        # mailbox (even archived), they've opted in and we should not silently
        # route via SMTP.
        if not self.env['x_microsoft.mailbox'].sudo().with_context(active_test=False).search_count([]):
            _logger.info("[Graph API] No mailboxes configured in system — falling back to standard mail handling")
            return super(MailMail, graph_mails).send(
                auto_commit=auto_commit,
                raise_exception=raise_exception,
                post_send_callback=post_send_callback,
            )

        # Mailboxes exist but none active/usable for this batch → cancel.
        # Protects production against unintended SMTP leakage when an admin
        # has set up Mail Pro but routing fails for a specific mail.
        if not graph_mails._is_mail_pro_configured():
            _logger.warning("[Graph API] Mailboxes exist but none active for this batch — cancelling")
            for mail in graph_mails:
                mail.write({'state': 'cancel'})
            return True

        _logger.info(f"[Graph API] send() called for {len(graph_mails)} email(s)")

        for mail in graph_mails:
            try:
                success, error_msg, error_code = mail._send_via_microsoft_graph()
                if success:
                    # Call post_send_callback if provided (Odoo 19 feature)
                    if post_send_callback:
                        post_send_callback(mail)
                elif error_code == 'no_recipients':
                    # This mail has no deliverable recipient — almost always an
                    # internal notification to a user/partner without an email
                    # address (e.g. the Administrator account). Standard Odoo
                    # silently drops such notifications; we must do the same and
                    # NOT raise, otherwise one undeliverable notification aborts
                    # the whole batch and blocks the real, deliverable emails
                    # composed alongside it.
                    _logger.info(
                        f"[Graph API] Email {mail.id} has no deliverable recipient "
                        f"— cancelling (not aborting batch)"
                    )
                    mail.write({'state': 'cancel'})
                    continue
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
            tuple: (success: bool, error_msg: str or None, error_code: str or None)
            `error_code` is a machine-readable tag for the failure (e.g.
            'no_recipients'); None for success or for unclassified errors.
        """
        self.ensure_one()

        _logger.info(f"[Graph API] Processing email {self.id}: subject='{self.subject}', to={self.email_to}")

        # Determine mailbox and account based on email type
        mailbox, account = self._get_mailbox_and_account()

        if not mailbox:
            error_msg = self._get_missing_mailbox_error()
            _logger.error(f"[Graph API] {error_msg}")
            self.write({
                'state': 'exception',
                'failure_reason': error_msg,
            })
            return (False, error_msg, None)

        if not account or not account.access_token:
            error_msg = self._get_missing_account_error(account)
            _logger.error(f"[Graph API] {error_msg}")
            self.write({
                'state': 'exception',
                'failure_reason': error_msg,
            })
            return (False, error_msg, None)

        _logger.info(f"[Graph API] Sending email {self.id} from mailbox {mailbox.email}")

        # Send via the mailbox's provider client, with the account's delegated
        # token (principle of least privilege).
        result = mailbox._get_client().send_message(
            mail_record=self,
            mailbox=mailbox,
            account=account,
        )

        if result['success']:
            # Store provider IDs for duplicate detection and threading
            provider_message_id = result.get('message_id')
            provider_thread_id = result.get('thread_id')

            self.write({
                'state': 'sent',
                'x_microsoft_message_id': provider_message_id,
                'x_microsoft_conversation_id': provider_thread_id,
            })

            # Also update mail_message for threading to work
            # When a reply comes in with an In-Reply-To header containing this
            # ID, we can find this message via x_microsoft_message_id
            if self.mail_message_id and provider_message_id:
                self.mail_message_id.write({
                    'x_microsoft_message_id': provider_message_id,
                    'x_microsoft_conversation_id': provider_thread_id,
                })
                _logger.info(f"[Graph API] Updated mail.message {self.mail_message_id.id} with provider IDs for threading")

            _logger.info(f"[Graph API] Email {self.id} sent successfully from {mailbox.email}")
            _logger.info(f"[Graph API] Stored provider IDs - Message: {provider_message_id}, Thread: {provider_thread_id}")
            return (True, None, None)
        else:
            # Mark as exception
            error_msg = result.get('error', 'Unknown error')
            error_code = result.get('error_code')
            self.write({
                'state': 'exception',
                'failure_reason': error_msg,
            })
            _logger.error(f"[Graph API] Email {self.id} failed to send: {error_msg}")
            return (False, error_msg, error_code)

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

    def _get_mailbox_and_account(self):
        """
        Determine which mailbox and account to use for sending.

        Resolution order:
        1. Internal user notification → notification mailbox
        2. Explicit composer "Send From" dropdown selection → that mailbox
        3. Author-based default → author's default mailbox

        For personal/shared mailboxes: sender uses their own OAuth token.
        For notification mailboxes: uses the owner's OAuth token.

        Returns:
            tuple: (mailbox, pan.mail.account) or (None, None) if not configured
        """
        self.ensure_one()

        # 1. For notifications to internal users, use notification mailbox
        if self._is_internal_user_notification():
            return self._get_notification_mailbox_and_account()

        # 2. Honor explicit "Send From" selection from the composer.
        #    The dropdown is a stronger signal than author_id heuristics, which
        #    misfire when a template's email_from matches the company partner
        #    (e.g. sale order quotations) instead of the actual sender.
        if self.x_microsoft_mailbox_id:
            mailbox = self.x_microsoft_mailbox_id
            # Defence in depth: creation already refused a mailbox the user was
            # not entitled to, but a row can predate that check or be written by
            # a migration. Fall through rather than raise — this runs in the
            # mail queue, where an exception would stall every other mail.
            author_user = self.author_id.user_ids[:1]
            if author_user and not mailbox._is_sendable_by(author_user):
                _logger.warning(
                    "[Graph API] Mail %s selects mailbox %s which its author %s may not use; "
                    "falling back to author routing",
                    self.id, mailbox.email, author_user.login,
                )
                mailbox = self.env['x_microsoft.mailbox']
            sender = self._resolve_account_for_mailbox(mailbox) if mailbox else None
            if sender and sender.connected:
                _logger.info(
                    f"[Graph API] Using explicitly selected mailbox: {mailbox.email} (sender: {sender.email})"
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
            return self._get_notification_mailbox_and_account()

        author_user = self.author_id.user_ids[0]

        if not author_user.x_microsoft_default_mailbox_id:
            _logger.info(f"[Graph API] User {author_user.name} has no default mailbox, falling back to notification mailbox")
            return self._get_notification_mailbox_and_account()

        mailbox = author_user.x_microsoft_default_mailbox_id

        # Ask the provider, exactly as the dropdown path does. On Microsoft a
        # shared mailbox still resolves to the author's own token (SendAs), so
        # this keeps today's behaviour; on Gmail it resolves to the mailbox's
        # service account, because there is no send-as to lend a token to.
        account = self._resolve_account_for_mailbox(mailbox)
        if not account.connected:
            _logger.info(
                f"[Graph API] No connected {mailbox._get_client().provider_label()} "
                f"account for {author_user.name}'s default mailbox, "
                f"falling back to notification mailbox"
            )
            return self._get_notification_mailbox_and_account()
        return (mailbox, account)

    def _resolve_account_for_mailbox(self, mailbox):
        """Pick the account whose token should send this mail from `mailbox`.

        Which credentials apply is provider-specific — Microsoft 365 lets a user
        send from a shared mailbox with their own token, while Gmail has no
        SendAs equivalent and resolves a shared mailbox to its own service
        account — so the decision belongs to the provider client.

        The author's user is passed in explicitly because it is the correct
        sender in cron context, where env.user is the cron runner.
        """
        self.ensure_one()
        author_user = self.author_id.user_ids[0] if self.author_id and self.author_id.user_ids else None
        return mailbox._get_client().resolve_sending_account(mailbox, author_user=author_user)

    def _get_notification_mailbox_and_account(self):
        """
        Get the notification mailbox (type='notification') and its owner's account.

        Returns:
            tuple: (mailbox, pan.mail.account) or (None, None) if not configured
        """
        # Find the notification mailbox by type
        mailbox = self.env['x_microsoft.mailbox'].search([
            ('x_mailbox_type', '=', 'notification'),
            ('active', '=', True),
        ], limit=1)

        if not mailbox:
            _logger.error("[Graph API] No notification mailbox configured")
            return (None, None)

        owner = self._resolve_account_for_mailbox(mailbox)
        if not owner:
            _logger.error(f"[Graph API] Notification mailbox {mailbox.email} has no owner configured")
            return (None, None)

        if not owner.connected:
            _logger.error(f"[Graph API] Notification mailbox account {owner.email} is not connected")
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
                    'Go to Settings → Mail Pro → Manage Mailbox List and create a mailbox with type "Notification".'
                )

            if not mailbox.x_owner_user_id:
                return _(
                    'Notification mailbox "%(email)s" has no Owner configured. '
                    'Edit the mailbox and select a user with %(provider)s connected.',
                    email=mailbox.email, provider=mailbox._get_client().provider_label(),
                )

            if not mailbox._has_working_credentials():
                return _(
                    'Notification mailbox owner "%(owner)s" has no %(provider)s account '
                    'connected. The user must connect it first.',
                    owner=mailbox.x_owner_user_id.name,
                    provider=mailbox._get_client().provider_label(),
                )

            return _('Unknown notification mailbox configuration error.')

        # Check specific failure reason for regular emails
        if not self.author_id:
            return _('Email has no author. Cannot determine which mailbox to use.')

        if not self.author_id.user_ids:
            return _(
                'Author "%s" is not linked to an Odoo user. '
                'Emails can only be sent by Odoo users with a connected email account.'
            ) % self.author_id.name

        user = self.author_id.user_ids[0]

        if not user.x_microsoft_default_mailbox_id:
            return _(
                'User "%s" has no default mailbox configured. '
                'Go to My Profile → Email and select a Default Send From mailbox.'
            ) % user.name

        mailbox = user.x_microsoft_default_mailbox_id

        # Whether the mailbox is usable is the provider's call, so the message
        # has to be too: on Microsoft a shared mailbox needs the *user* connected
        # plus SendAs, on Gmail it needs the shared address authorized itself.
        client = mailbox._get_client()
        if not self._resolve_account_for_mailbox(mailbox).connected:
            provider = client.provider_label()
            if mailbox.x_mailbox_type == 'shared' and client.supports_shared_mailbox:
                return _(
                    'User "%(user)s" has no connected %(provider)s account. '
                    'To send from shared mailbox "%(email)s", connect your account '
                    'and ensure you have SendAs permission.',
                    user=user.name, provider=provider, email=mailbox.email,
                )
            if mailbox.x_mailbox_type == 'shared':
                return _(
                    'Shared mailbox "%(email)s" has no connected %(provider)s account. '
                    'Authorize %(email)s itself — on %(provider)s a shared address is '
                    'its own account.',
                    email=mailbox.email, provider=provider,
                )
            return _(
                'User "%(user)s" has no connected %(provider)s account. '
                'Go to My Profile → Mail Pro and connect it.',
                user=user.name, provider=provider,
            )

        return _('Unknown mailbox configuration error.')

    def _get_missing_account_error(self, account):
        """Generate appropriate error message for missing account credentials."""
        self.ensure_one()

        if self._is_internal_user_notification():
            return _(
                'Notification sender not configured or not connected to Microsoft. '
                'Go to Settings → Mail Pro and configure the Notification Sender.'
            )

        if not account:
            return _('No connected email account found. Author must be linked to an Odoo user with Microsoft connected.')

        if not account.access_token:
            return _(
                'Account "%s" has no valid Microsoft access token. '
                'Please reconnect Microsoft account in My Profile → Email.'
            ) % account.email

        return _('Unknown account configuration error.')
