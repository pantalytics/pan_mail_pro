# -*- coding: utf-8 -*-
from odoo import fields, models, api, _
from odoo.exceptions import ValidationError

from .mail_mail import INTERACTIVE_SEND


class MailComposeMessage(models.TransientModel):
    """Extend the composer with the "Send From" mailbox choice."""
    _inherit = 'mail.compose.message'

    x_send_from_mailbox_id = fields.Many2one(
        'pan.mail.mailbox',
        string='Send From',
        help='Which mailbox sends this email'
        # Domain is set dynamically in the view to filter by owner for personal mailboxes
    )

    x_setup_warning = fields.Char(
        string='Setup Warning',
        compute='_compute_setup_warning',
        store=False
    )

    @api.constrains('x_send_from_mailbox_id')
    def _check_send_from_permission(self):
        """Enforce the composer's view domain server-side.

        The domain on this field lives in the view (mail_compose_message_views.xml),
        which filters what the dropdown *offers*. It does not constrain what the
        field can be set to over RPC, and a personal mailbox sends with its
        owner's token — so without this check any internal user can send mail as
        a colleague.
        """
        for record in self:
            mailbox = record.x_send_from_mailbox_id
            if mailbox and not mailbox._is_sendable_by(self.env.user):
                raise ValidationError(_(
                    "You cannot send from %(mailbox)s. Personal mailboxes can "
                    "only be used by their owner.",
                    mailbox=mailbox.email,
                ))

    def _action_send_mail(self, auto_commit=False):
        """The one place a send *is* what the person did. `mail.mail.send()`
        raises the failure at them here, and records it silently everywhere
        else (see `_is_the_action`)."""
        return super(MailComposeMessage, self.with_context(
            **{INTERACTIVE_SEND: True}))._action_send_mail(auto_commit=auto_commit)

    @api.depends('x_send_from_mailbox_id')
    @api.depends_context('uid')
    def _compute_setup_warning(self):
        """Check if the user still needs to connect an email account.

        Any provider counts: a Gmail-only user is set up, and telling them to
        connect Microsoft would be wrong. And a composer that already has a
        sender (the Inbox passes the mailbox being read) has nothing to warn
        about: the warning is for the empty dropdown, not for the preference.
        """
        user = self.env.user
        for record in self:
            if not user.x_pan_mail_account_ids.filtered('connected'):
                record.x_setup_warning = _("Connect your email account: My Preferences, Connect Mailbox.")
            elif record.x_send_from_mailbox_id:
                record.x_setup_warning = False
            elif not user.x_default_mailbox_id:
                record.x_setup_warning = _("Pick a default mailbox: My Preferences, Send from.")
            else:
                record.x_setup_warning = False

    @api.model
    def default_get(self, fields_list):
        """Set default mailbox from user preferences"""
        result = super().default_get(fields_list)

        if 'x_send_from_mailbox_id' in fields_list:
            user = self.env.user
            # The caller's choice first (the Inbox passes the mailbox being
            # read), the user's default otherwise. Either is used only while
            # it is active and still one they may send from. Silently dropping
            # a stale one is better than opening the composer straight into a
            # ValidationError -- the default may have been set before the
            # mailbox changed type or owner, and the Inbox shows mailboxes a
            # manager may read but not send from.
            asked = result.get('x_send_from_mailbox_id')
            candidates = [self.env['pan.mail.mailbox'].browse(asked)] if asked else []
            candidates.append(user.x_default_mailbox_id)
            result.pop('x_send_from_mailbox_id', None)
            for mailbox in candidates:
                if mailbox and mailbox.exists() and mailbox.active \
                        and mailbox.sudo()._is_sendable_by(user):
                    result['x_send_from_mailbox_id'] = mailbox.id
                    break

        return result

    def action_send_mail(self):
        """Override to pass selected mailbox via context to mail.mail creation."""
        # Pass the selected mailbox via context so mail.mail.create() can use it
        if self.x_send_from_mailbox_id:
            self = self.with_context(send_from_mailbox_id=self.x_send_from_mailbox_id.id)

        return super().action_send_mail()

    def _action_send_mail_comment(self, res_ids):
        """Post chatter message with mailbox context."""
        # Pass mailbox via context
        if self.x_send_from_mailbox_id:
            self = self.with_context(send_from_mailbox_id=self.x_send_from_mailbox_id.id)

        return super()._action_send_mail_comment(res_ids)
