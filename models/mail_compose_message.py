# -*- coding: utf-8 -*-
from odoo import fields, models, api, _
from odoo.exceptions import ValidationError


class MailComposeMessage(models.TransientModel):
    """Extend mail composer to add Microsoft mailbox selection"""
    _inherit = 'mail.compose.message'

    x_microsoft_send_from_id = fields.Many2one(
        'x_microsoft.mailbox',
        string='Send From',
        help='Select which Microsoft mailbox to send this email from'
        # Domain is set dynamically in the view to filter by owner for personal mailboxes
    )

    x_microsoft_setup_warning = fields.Char(
        string='Setup Warning',
        compute='_compute_microsoft_setup_warning',
        store=False
    )

    @api.constrains('x_microsoft_send_from_id')
    def _check_send_from_permission(self):
        """Enforce the composer's view domain server-side.

        The domain on this field lives in the view (mail_compose_message_views.xml),
        which filters what the dropdown *offers*. It does not constrain what the
        field can be set to over RPC, and a personal mailbox sends with its
        owner's token — so without this check any internal user can send mail as
        a colleague.
        """
        for record in self:
            mailbox = record.x_microsoft_send_from_id
            if mailbox and not mailbox._is_sendable_by(self.env.user):
                raise ValidationError(_(
                    "You cannot send from %(mailbox)s. Personal mailboxes can "
                    "only be used by their owner.",
                    mailbox=mailbox.email,
                ))

    @api.depends_context('uid')
    def _compute_microsoft_setup_warning(self):
        """Check if the user still needs to connect an email account.

        Any provider counts: a Gmail-only user is set up, and telling them to
        connect Microsoft would be wrong.
        """
        user = self.env.user
        for record in self:
            if not user.x_pan_mail_account_ids.filtered('connected'):
                record.x_microsoft_setup_warning = "Connect your email account in My Preferences → Mail Pro tab."
            elif not user.x_microsoft_default_mailbox_id:
                record.x_microsoft_setup_warning = "Select a default mailbox in My Preferences → Mail Pro tab."
            else:
                record.x_microsoft_setup_warning = False

    @api.model
    def default_get(self, fields_list):
        """Set default mailbox from user preferences"""
        result = super().default_get(fields_list)

        if 'x_microsoft_send_from_id' in fields_list:
            user = self.env.user
            default_mailbox = user.x_microsoft_default_mailbox_id
            # Use the user's default mailbox if set, still active, and still one
            # they may send from. Silently dropping a stale default is better
            # than opening the composer straight into a ValidationError — the
            # default may have been set before the mailbox changed type or owner.
            if (default_mailbox and default_mailbox.active
                    and default_mailbox.sudo()._is_sendable_by(user)):
                result['x_microsoft_send_from_id'] = default_mailbox.id

        return result

    def action_send_mail(self):
        """Override to pass selected mailbox via context to mail.mail creation."""
        # Pass the selected mailbox via context so mail.mail.create() can use it
        if self.x_microsoft_send_from_id:
            self = self.with_context(microsoft_mailbox_id=self.x_microsoft_send_from_id.id)

        return super().action_send_mail()

    def _action_send_mail_comment(self, res_ids):
        """Post chatter message with mailbox context."""
        # Pass mailbox via context
        if self.x_microsoft_send_from_id:
            self = self.with_context(microsoft_mailbox_id=self.x_microsoft_send_from_id.id)

        return super()._action_send_mail_comment(res_ids)
