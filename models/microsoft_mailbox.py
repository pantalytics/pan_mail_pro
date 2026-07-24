# -*- coding: utf-8 -*-
import logging
import re
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
    x_provider = fields.Selection(
        [('microsoft', 'Microsoft 365'), ('google', 'Google Workspace')],
        string='Provider',
        required=True,
        default='microsoft',
        help='Which email service backs this mailbox.'
    )

    # -------------------------------------------------------------------------
    # Mailbox Type Configuration
    # -------------------------------------------------------------------------
    x_mailbox_type = fields.Selection([
        ('personal', 'Personal'),
        ('shared', 'Shared'),
        ('notification', 'Notification'),
    ], string='Type', default='personal', required=True,
        help='Personal: Only the owner can send from this mailbox\n'
             'Shared: All users can send using their own Microsoft account\n'
             'Notification: Used for system emails, owner\'s account is used to send')

    x_owner_user_id = fields.Many2one(
        'res.users',
        string='Owner',
        domain="['|', ('x_microsoft_oauth_connected', '=', True), ('x_google_oauth_connected', '=', True)]",
        help='Personal mailbox: the user who owns and sends from this mailbox.\n'
             'Notification mailbox: the user whose OAuth token is used to send system emails.',
        index=True
    )

    # -------------------------------------------------------------------------
    # Incoming Mail Configuration
    # -------------------------------------------------------------------------
    x_incoming_user_id = fields.Many2one(
        'res.users',
        string='Sync As User',
        domain="['|', ('x_microsoft_oauth_connected', '=', True), ('x_google_oauth_connected', '=', True)]",
        help='User whose account is used to fetch emails.'
    )
    x_incoming_enabled = fields.Boolean(
        string='Enable Incoming Sync',
        compute='_compute_incoming_enabled',
        store=True,
        help='Automatically enabled when a sync user is selected'
    )
    # x_sync_mode is the source of truth, kept for backwards compatibility
    x_sync_mode = fields.Selection([
        ('none', 'Send messages only'),
        ('known_partners', 'Send and receive messages from existing contacts'),
        ('all', 'Send and receive all messages'),
    ], string='Sync Mode', default='none')

    # -------------------------------------------------------------------------
    # Simplified UI fields (Apple-style progressive disclosure)
    # These compute from / write to x_sync_mode
    # -------------------------------------------------------------------------
    x_incoming_sync = fields.Boolean(
        string='Enable',
        compute='_compute_incoming_sync',
        inverse='_inverse_incoming_sync',
        store=True,
        help='Sync incoming emails from this mailbox to Odoo'
    )
    x_sync_unknown_contacts = fields.Boolean(
        string='Include',
        compute='_compute_sync_unknown_contacts',
        inverse='_inverse_sync_unknown_contacts',
        store=True,
        help='Also sync emails from senders not yet in Odoo'
    )

    @api.depends('x_sync_mode')
    def _compute_incoming_sync(self):
        for record in self:
            record.x_incoming_sync = record.x_sync_mode != 'none'

    def _inverse_incoming_sync(self):
        for record in self:
            if not record.x_incoming_sync:
                record.x_sync_mode = 'none'
            elif record.x_sync_unknown_contacts:
                record.x_sync_mode = 'all'
            else:
                record.x_sync_mode = 'known_partners'

    @api.depends('x_sync_mode')
    def _compute_sync_unknown_contacts(self):
        for record in self:
            record.x_sync_unknown_contacts = record.x_sync_mode == 'all'

    def _inverse_sync_unknown_contacts(self):
        for record in self:
            if record.x_incoming_sync:
                record.x_sync_mode = 'all' if record.x_sync_unknown_contacts else 'known_partners'

    # -------------------------------------------------------------------------
    # Routing Configuration (for new incoming emails, not replies)
    # -------------------------------------------------------------------------
    x_routing_smart = fields.Boolean(
        string='AI Routing',
        default=False,
        help='Let AI decide where to route (CRM, Helpdesk, etc.)'
    )

    x_route_to_team = fields.Boolean(
        string='To Team',
        default=False,
        help='Route to a team instead of contact chatter'
    )

    x_queue_unknown_contacts = fields.Boolean(
        string='Queue for Review',
        default=False,
        help='Hold for manual review instead of auto-creating contacts'
    )

    x_exclude_internal = fields.Boolean(
        string='Exclude Internal',
        default=True,
        help='Skip emails from your company domain. Disable for team mailboxes where internal forwarding should be logged.'
    )
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

    @api.depends('x_sync_mode', 'x_owner_user_id')
    def _compute_incoming_enabled(self):
        """Incoming sync is enabled when sync_mode is set and owner has OAuth."""
        for record in self:
            record.x_incoming_enabled = (
                record.x_sync_mode in ('known_partners', 'all') and
                bool(record.x_owner_user_id) and
                record.x_owner_user_id.x_microsoft_oauth_connected
            )

    @api.depends('x_sync_mode')
    def _compute_sync_folders(self):
        """Sync modes enable both Inbox + Sent Items."""
        for record in self:
            sync_enabled = record.x_sync_mode in ('known_partners', 'all')
            record.x_sync_inbox = sync_enabled
            record.x_sync_sent = sync_enabled
    x_sync_start_date = fields.Datetime(
        string='Import From',
        default=fields.Datetime.now,
        help='Import emails starting from this date. Default is today.'
    )
    x_last_sync_date = fields.Datetime(
        string='Last Synced',
        readonly=True,
        help='Timestamp of last successful sync'
    )
    x_alias_id = fields.Many2one(
        'mail.alias',
        string='Route to Team',
        domain="[('alias_name', '!=', False)]",
        help='Select the team where emails should be routed. Teams configure their alias in their own settings (e.g., Sales Team → Alias).'
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

    # -------------------------------------------------------------------------
    # Health Status (computed for list view)
    # -------------------------------------------------------------------------
    x_health_status = fields.Selection([
        ('healthy', 'OK'),
        ('warning', 'Warning'),
        ('error', 'Error'),
    ], string='Status', compute='_compute_health_status', store=False)

    @api.depends('state', 'x_sync_mode', 'x_mailbox_type', 'x_owner_user_id', 'x_owner_user_id.x_microsoft_oauth_connected')
    def _compute_health_status(self):
        """Compute health status based on state and configuration."""
        for record in self:
            # Check 1: Sync errors
            if record.state == 'error':
                record.x_health_status = 'error'
                continue

            # Check 2: Owner OAuth status (for personal/notification mailboxes)
            if record.x_mailbox_type in ('personal', 'notification'):
                if not record.x_owner_user_id:
                    record.x_health_status = 'error'
                    continue
                if not record.x_owner_user_id.x_microsoft_oauth_connected:
                    record.x_health_status = 'error'
                    continue

            # Check 3: Shared mailbox with sync enabled needs connected owner
            if record.x_mailbox_type == 'shared' and record.x_sync_mode != 'none':
                if record.x_owner_user_id and not record.x_owner_user_id.x_microsoft_oauth_connected:
                    record.x_health_status = 'error'
                    continue

            # Check 4: Sync enabled but never synced yet
            if record.x_sync_mode != 'none' and record.state == 'draft':
                record.x_health_status = 'warning'
                continue

            # All checks passed
            record.x_health_status = 'healthy'

    def action_test_incoming(self):
        """Test incoming mail configuration by fetching a few messages."""
        self.ensure_one()

        if not self.x_owner_user_id:
            raise UserError(_('Please select an Owner for this mailbox.'))

        if not self.x_owner_user_id.x_microsoft_oauth_connected:
            raise UserError(_('The Owner is not connected to Microsoft. '
                              'Please connect their account first.'))

        graph_client = self.env['microsoft.graph.client']

        try:
            # Try to fetch 1 message to test connection
            messages = graph_client.fetch_messages(
                account=self._get_provider()._account_for_user(self.x_owner_user_id),
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

        if not self.x_owner_user_id:
            raise UserError(_('Please select an Owner first.'))

        if not self.x_owner_user_id.x_microsoft_oauth_connected:
            raise UserError(_('Owner "%s" is not connected to Microsoft.') % self.x_owner_user_id.name)

        # Trigger the processor for this mailbox
        processor = self.env['microsoft.incoming.mail.processor']
        processor._process_mailbox(self)

        # Mark as active on success (clear any previous error)
        if self.state != 'active':
            self.write({'state': 'active', 'x_error_message': False})

        # Reload the form to show updated status
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'x_microsoft.mailbox',
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }

    def write(self, vals):
        """Reset x_last_sync_date when x_sync_start_date is moved to an earlier date."""
        if 'x_sync_start_date' in vals and vals['x_sync_start_date']:
            new_start = fields.Datetime.to_datetime(vals['x_sync_start_date'])
            for record in self:
                if record.x_last_sync_date and new_start < record.x_last_sync_date:
                    vals['x_last_sync_date'] = new_start
        return super().write(vals)

    @api.onchange('x_sync_mode')
    def _onchange_sync_mode(self):
        """Reset state when switching to no sync."""
        if self.x_sync_mode == 'none':
            self.state = 'draft'
            self.x_error_message = False

    @api.constrains('email')
    def _check_email_format(self):
        """Validate email format"""
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

    @api.constrains('x_mailbox_type', 'x_owner_user_id', 'x_sync_mode')
    def _check_owner_required(self):
        """Ensure owner is set when required."""
        for record in self:
            if record.x_mailbox_type in ('personal', 'notification') and not record.x_owner_user_id:
                raise ValidationError(_(
                    '%s mailbox requires an Owner. '
                    'Please select a user with Microsoft OAuth connected.'
                ) % record.x_mailbox_type.capitalize())
            # Shared mailbox with sync enabled requires owner
            if (record.x_mailbox_type == 'shared' and
                    record.x_sync_mode != 'none' and
                    not record.x_owner_user_id):
                raise ValidationError(_(
                    'Shared mailbox with sync enabled requires an Owner. '
                    'The Owner\'s Microsoft account will be used to read emails.'
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

    @api.constrains('x_sync_mode')
    def _check_notification_mailbox_for_sync(self):
        """Ensure notification mailbox exists when enabling incoming sync."""
        for record in self:
            if record.x_sync_mode != 'none' and record.x_mailbox_type != 'notification':
                notification_mailbox = self.search([
                    ('x_mailbox_type', '=', 'notification'),
                    ('active', '=', True),
                ], limit=1)
                if not notification_mailbox:
                    raise ValidationError(_(
                        'A Notification mailbox is required for incoming email sync. '
                        'Please create a mailbox with type "Notification" first.'
                    ))

    @api.constrains('x_route_to_team', 'x_alias_id')
    def _check_alias_required_for_team_routing(self):
        """Ensure alias is set when route_to_team is enabled."""
        for record in self:
            if record.x_route_to_team and not record.x_alias_id:
                raise ValidationError(_(
                    'A Team must be selected when "Route to Team" is enabled.'
                ))

    @api.constrains('x_routing_smart')
    def _check_smart_routing_not_implemented(self):
        """Prevent enabling smart routing until AI routing is implemented."""
        for record in self:
            if record.x_routing_smart:
                raise ValidationError(_(
                    'Smart AI Routing is not yet implemented. This feature will be available in a future release.'
                ))

    @api.constrains('x_queue_unknown_contacts')
    def _check_queue_unknown_contacts_not_implemented(self):
        """Prevent enabling queue for review until the feature is implemented."""
        for record in self:
            if record.x_queue_unknown_contacts:
                raise ValidationError(_(
                    'Queue for Review is not yet implemented. This feature will be available in a future release.'
                ))

    def _get_provider(self):
        """Return the provider implementation serving this mailbox.

        Providers are AbstractModels, so this is a registry lookup rather than a
        record: `x_provider` names which one.

        Returns:
            pan.mail.provider.base: the provider (stateless)
        """
        self.ensure_one()
        return self.env['pan.mail.provider.%s' % self.x_provider]

    def get_graph_user_id(self):
        """
        Get the identifier to use for Microsoft Graph API calls.

        Returns the email address for use in /users/{id}/sendMail calls.

        Returns:
            str: Email address for use in /users/{id}/... calls
        """
        self.ensure_one()
        return self.email
