# -*- coding: utf-8 -*-
import logging
from odoo import fields, models, api, _
from odoo.exceptions import ValidationError, UserError

_logger = logging.getLogger(__name__)


class MicrosoftMailbox(models.Model):
    """Model to store available Microsoft mailboxes for sending and receiving emails"""
    _name = 'x_microsoft.mailbox'
    _description = 'Microsoft Mailbox'
    _order = 'sequence, email'
    _rec_name = 'email'

    email = fields.Char(
        string='Email Address',
        required=True,
        help='Full email address of the mailbox (e.g., support@company.com)'
    )
    sequence = fields.Integer(
        string='Sequence',
        default=10,
        help='Display order in dropdowns'
    )
    active = fields.Boolean(
        string='Active',
        default=True,
        help='Uncheck to hide this mailbox from users'
    )

    # -------------------------------------------------------------------------
    # Mailbox Type Configuration
    # -------------------------------------------------------------------------
    x_mailbox_type = fields.Selection([
        ('personal', 'Personal Mailbox'),
        ('shared', 'Shared Mailbox'),
        ('notification', 'Notification Mailbox'),
    ], string='Mailbox Type', default='personal', required=True,
        help='Personal: User sends from their own mailbox (e.g., john@company.com)\n'
             'Shared: Multiple users can send from this mailbox (e.g., support@company.com)\n'
             'Notification: System notifications mailbox (e.g., notifications@company.com)')

    x_owner_user_id = fields.Many2one(
        'res.users',
        string='Owner',
        help='For personal mailboxes: the user who owns this mailbox. '
             'Only this user can see and send from this mailbox.',
        index=True
    )

    x_sending_user_id = fields.Many2one(
        'res.users',
        string='Sending User',
        domain="[('x_microsoft_oauth_connected', '=', True)]",
        help='User whose Microsoft account is used to send emails from this mailbox. '
             'Required for Shared and Notification mailboxes. '
             'For Personal mailboxes, the mailbox owner sends with their own account.'
    )

    # -------------------------------------------------------------------------
    # Incoming Mail Configuration
    # -------------------------------------------------------------------------
    x_incoming_user_id = fields.Many2one(
        'res.users',
        string='Sync As User',
        domain="[('x_microsoft_oauth_connected', '=', True)]",
        help='User whose Microsoft account is used to fetch emails. Must have Mail.Read permission.'
    )
    x_incoming_enabled = fields.Boolean(
        string='Enable Incoming Sync',
        compute='_compute_incoming_enabled',
        store=True,
        help='Automatically enabled when a sync user is selected'
    )
    x_sync_mode = fields.Selection([
        ('none', 'No sync (outgoing only)'),
        ('1way', 'Received emails only'),
        ('2way', 'Received + Sent from Outlook'),
    ], string='Sync Mode', default='none',
        help='None: Only use this mailbox for sending emails.\n'
             '1-way: Sync emails received in this mailbox to Odoo.\n'
             '2-way: Also sync emails sent from Outlook back to Odoo.')
    # Keep for backwards compatibility / internal use
    x_sync_inbox = fields.Boolean(
        string='Sync Inbox',
        default=True,
        compute='_compute_sync_folders',
        store=True
    )
    x_sync_sent = fields.Boolean(
        string='Sync Sent Items',
        default=True,
        compute='_compute_sync_folders',
        store=True
    )

    @api.depends('x_sync_mode', 'x_incoming_user_id')
    def _compute_incoming_enabled(self):
        for record in self:
            record.x_incoming_enabled = (
                record.x_sync_mode in ('1way', '2way') and
                bool(record.x_incoming_user_id)
            )

    @api.depends('x_sync_mode')
    def _compute_sync_folders(self):
        for record in self:
            record.x_sync_inbox = record.x_sync_mode in ('1way', '2way')
            record.x_sync_sent = record.x_sync_mode == '2way'
    x_last_sync_date = fields.Datetime(
        string='Last Sync',
        readonly=True,
        help='Timestamp of last successful sync'
    )
    x_create_activity = fields.Boolean(
        string='Create Activity for New Emails',
        default=True,
        help='Create a "Review Email" activity when a new email arrives'
    )
    x_activity_user_id = fields.Many2one(
        'res.users',
        string='Assign Activities To',
        help='User who receives activities for new emails. Leave empty for no assignment.'
    )
    state = fields.Selection([
        ('draft', 'Not Configured'),
        ('active', 'Active'),
        ('error', 'Error'),
    ], string='Sync Status', default='draft', readonly=True)
    x_error_message = fields.Text(
        string='Last Error',
        readonly=True,
        help='Error message from last failed sync attempt'
    )

    def action_test_incoming(self):
        """Test incoming mail configuration by fetching a few messages."""
        self.ensure_one()

        if not self.x_incoming_user_id:
            raise UserError(_('Please select a user to sync as.'))

        if not self.x_incoming_user_id.x_microsoft_oauth_connected:
            raise UserError(_('The selected user is not connected to Microsoft. '
                              'Please connect their account first.'))

        graph_client = self.env['microsoft.graph.client']

        try:
            # Try to fetch 1 message to test connection
            messages = graph_client.fetch_messages(
                user=self.x_incoming_user_id,
                mailbox_email=self.email,
                folder='Inbox',
                top=1
            )

            self.write({
                'state': 'active',
                'x_error_message': False,
            })

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Connection Successful'),
                    'message': _('Successfully connected to mailbox %s. Found %d message(s) in test.') % (
                        self.email, len(messages)),
                    'type': 'success',
                    'sticky': False,
                }
            }

        except Exception as e:
            self.write({
                'state': 'error',
                'x_error_message': str(e),
            })

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Connection Failed'),
                    'message': str(e),
                    'type': 'danger',
                    'sticky': True,
                }
            }

    def action_sync_now(self):
        """Manually trigger email sync for this mailbox."""
        self.ensure_one()

        if self.x_sync_mode == 'none':
            raise UserError(_('Sync mode is set to "No sync". Change it to enable syncing.'))

        if not self.x_incoming_user_id:
            raise UserError(_('Please select a Sync User first.'))

        # Trigger the processor for this mailbox
        processor = self.env['microsoft.incoming.mail.processor']
        processor._process_mailbox(self)

        # Reload the form to show updated last sync time
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'x_microsoft.mailbox',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    @api.onchange('x_sync_mode')
    def _onchange_sync_mode(self):
        """Clear sync user when switching to no sync."""
        if self.x_sync_mode == 'none':
            self.x_incoming_user_id = False
            self.state = 'draft'
            self.x_error_message = False

    @api.onchange('x_incoming_user_id')
    def _onchange_incoming_user_id(self):
        """Reset state when sync user is cleared."""
        if not self.x_incoming_user_id:
            self.state = 'draft'
            self.x_error_message = False

    @api.constrains('email')
    def _check_email_format(self):
        """Validate email format"""
        import re
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        for record in self:
            if record.email and not re.match(email_pattern, record.email):
                raise ValidationError(_('Invalid email address format: %s') % record.email)

    @api.constrains('email')
    def _check_email_unique(self):
        """Ensure email is unique"""
        for record in self:
            if record.email:
                existing = self.search([
                    ('email', '=', record.email),
                    ('id', '!=', record.id)
                ], limit=1)
                if existing:
                    raise ValidationError(_('This email address is already registered!'))

    @api.constrains('x_mailbox_type', 'x_sending_user_id')
    def _check_sending_user_required(self):
        """Ensure notification mailboxes have a sending user configured."""
        for record in self:
            # Only notification mailboxes require a configured sending user
            # Shared mailboxes: each user sends with their own OAuth token
            # Personal mailboxes: owner sends with their own OAuth token
            if record.x_mailbox_type == 'notification' and not record.x_sending_user_id:
                raise ValidationError(_(
                    'Notification mailbox requires a Sending User. '
                    'Please select a user with Microsoft OAuth connected.'
                ))

    @api.constrains('x_mailbox_type')
    def _check_single_notification_mailbox(self):
        """Ensure only one notification mailbox exists."""
        for record in self:
            if record.x_mailbox_type == 'notification':
                existing = self.search([
                    ('x_mailbox_type', '=', 'notification'),
                    ('id', '!=', record.id),
                    ('active', '=', True),
                ], limit=1)
                if existing:
                    raise ValidationError(_(
                        'Only one active Notification mailbox is allowed. '
                        'Existing notification mailbox: %s'
                    ) % existing.email)

    def get_sending_user(self):
        """
        Get the user whose OAuth token should be used for sending from this mailbox.

        Returns:
            res.users: The user to use for sending, or False if current user should be used
        """
        self.ensure_one()
        if self.x_mailbox_type == 'notification':
            # Notification mailbox: use the configured sending user for system emails
            return self.x_sending_user_id
        else:
            # Personal and Shared mailboxes: current user sends with their own OAuth token
            # For shared mailboxes, user needs Mail.Send.Shared permission + SendAs rights in M365
            return False

    def get_graph_user_id(self):
        """
        Get the identifier to use for Microsoft Graph API calls.

        Returns the email address for use in /users/{id}/sendMail calls.

        Returns:
            str: Email address for use in /users/{id}/... calls
        """
        self.ensure_one()
        return self.email
