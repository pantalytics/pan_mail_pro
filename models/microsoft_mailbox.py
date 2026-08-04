# -*- coding: utf-8 -*-
import logging
import re
from odoo import fields, models, api, _
from odoo.exceptions import ValidationError, UserError
from .mail_provider_client import (
    DEFAULT_PROVIDER,
    FOLDER_INBOX,
    PROVIDER_SELECTION,
    get_provider_client,
)

_logger = logging.getLogger(__name__)


class MicrosoftMailbox(models.Model):
    """Model to store available Microsoft mailboxes for sending and receiving emails"""
    _name = 'x_microsoft.mailbox'
    _description = 'Microsoft Mailbox'
    _order = 'sequence, email'
    _rec_name = 'email'

    # Every mailbox is serviced by exactly one provider client, resolved
    # through the registry in mail_provider_client.py.
    x_provider = fields.Selection(
        PROVIDER_SELECTION,
        string='Provider',
        default=DEFAULT_PROVIDER,
        required=True,
        help='Email provider that services this mailbox.',
    )

    def _get_client(self):
        """Return the provider client for this mailbox."""
        self.ensure_one()
        return get_provider_client(self.env, self.x_provider)

    def _is_sendable_by(self, user):
        """Whether `user` may choose this mailbox as the sender of a mail.

        A personal mailbox sends with its *owner's* delegated token (see
        `_resolve_sending_user` in the Microsoft client), so letting anyone pick
        one lets any internal user send mail as a colleague, signed by that
        colleague's own token. Microsoft does not stop it: it is not a SendAs,
        it is the owner's credentials being used directly.

        The composer's view domain expresses the same rule, but a view domain is
        a convenience for the UI, never a boundary — the field is writable over
        RPC. This method is the boundary.

        Only personal mailboxes are restricted. A notification mailbox also
        sends with its owner's token, but that is documented behaviour the whole
        module rests on — every internal notification goes out through it, from
        any author. Restricting it here broke
        test_05_dropdown_notification_uses_notification_owner, and rightly so:
        the hole was somebody sending as a named colleague, not the company's
        system-mail address doing what it exists to do.

        Shared mailboxes are shared on purpose.
        """
        self.ensure_one()
        if not self.active:
            return False
        if self.x_mailbox_type == 'personal':
            return bool(self.x_owner_user_id) and self.x_owner_user_id == user
        return True

    def _has_working_credentials(self):
        """Whether this mailbox can actually reach its provider right now.

        Deliberately not "is the owner connected to Microsoft". Which credentials
        a mailbox runs on is the provider's decision: Microsoft reads a mailbox
        with its owner's delegated token, while a Gmail shared mailbox is its own
        Workspace account and has no owner to ask. Asking the client keeps that
        difference in the one place that is allowed to know about it.
        """
        self.ensure_one()
        return bool(self._get_client().resolve_receiving_account(self).connected)

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
        ('personal', 'Personal'),
        ('shared', 'Shared'),
        ('notification', 'Notification'),
    ], string='Type', default='personal', required=True,
        help='Personal: Only the owner can send from this mailbox\n'
             'Shared: All users send from this address; which credentials are used '
             'depends on the provider\n'
             'Notification: Used for system emails, owner\'s account is used to send')

    x_owner_user_id = fields.Many2one(
        'res.users',
        string='Owner',
        domain="[('x_pan_mail_connected', '=', True)]",
        help='Personal mailbox: the user who owns and sends from this mailbox.\n'
             'Notification mailbox: the user whose OAuth token is used to send system emails.',
        index=True
    )

    # -------------------------------------------------------------------------
    # Incoming Mail Configuration
    # -------------------------------------------------------------------------
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

    @api.depends('x_sync_mode', 'x_provider', 'x_owner_user_id',
                 'x_owner_user_id.x_pan_mail_account_ids.connected')
    def _compute_incoming_enabled(self):
        """Incoming sync is enabled when sync_mode is set and the mailbox has
        credentials its provider can actually use.

        The account is in `depends` on purpose: the old version depended only on
        the mode and the owner, so connecting OAuth *after* configuring the
        mailbox never flipped this field back on.

        Known limit: a Gmail shared mailbox runs on a service account found by
        address, not reachable by any field path from here, so authorizing one
        after the fact does not retrigger this compute. It is correct at create
        time and whenever the mailbox is edited; a stored field cannot depend on
        a searched relation.
        """
        for record in self:
            record.x_incoming_enabled = (
                record.x_sync_mode in ('known_partners', 'all')
                and record._has_working_credentials()
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

    @api.depends('state', 'x_sync_mode', 'x_mailbox_type', 'x_provider', 'x_owner_user_id',
                 'x_owner_user_id.x_pan_mail_account_ids.connected')
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
                if not record._has_working_credentials():
                    record.x_health_status = 'error'
                    continue

            # Check 3: Shared mailbox with sync enabled needs connected owner
            if record.x_mailbox_type == 'shared' and record.x_sync_mode != 'none':
                if not record._has_working_credentials():
                    record.x_health_status = 'error'
                    continue

            # Check 4: Sync enabled but never synced yet
            if record.x_sync_mode != 'none' and record.state == 'draft':
                record.x_health_status = 'warning'
                continue

            # All checks passed
            record.x_health_status = 'healthy'

    def _no_credentials_error(self, sender=None):
        """Why this mailbox has no usable credentials, in the provider's terms.

        The single explanation for every "cannot send / cannot read" in the
        module. `sender` is the user whose token was expected, where the caller
        knows — on a Microsoft shared mailbox that is the author rather than the
        owner, and naming the wrong person sends an admin looking in the wrong
        place.
        """
        self.ensure_one()
        client = self._get_client()
        provider = client.provider_label()

        if self.x_mailbox_type == 'shared':
            if not client.supports_shared_mailbox:
                # Gmail and IMAP: a shared address is its own account, so there
                # is nothing an owner could connect on its behalf.
                return _(
                    'Shared mailbox "%(email)s" has no credentials of its own. On '
                    '%(provider)s a shared address is its own account, not a '
                    'delegation of someone else\'s.',
                    email=self.email, provider=provider,
                )
            who = sender or self.x_owner_user_id
            if not who:
                return _(
                    'Nobody is connected who could send from shared mailbox "%s".'
                ) % self.email
            return _(
                '"%(who)s" has no connected %(provider)s account, so nothing can '
                'send from shared mailbox "%(email)s". Connect it under My Profile '
                '→ Mail Pro, with SendAs rights on that address.',
                who=who.name, provider=provider, email=self.email,
            )

        if not self.x_owner_user_id:
            return _(
                'Mailbox "%s" has no Owner. Select the user whose account it '
                'sends and receives with.'
            ) % self.email
        return _(
            'Owner "%(owner)s" has no connected %(provider)s account. '
            'They must connect it first.',
            owner=self.x_owner_user_id.name, provider=provider,
        )

    def action_open_account(self):
        """Open the email account serving this mailbox, or a prefilled new one.

        Only meaningful for providers whose credentials are typed in rather than
        granted through a consent screen: an IMAP mailbox is useless until
        somebody enters its server and password, and this is the shortest path
        from the mailbox to that form.
        """
        self.ensure_one()
        account = self._get_client().resolve_receiving_account(self)
        action = {
            'type': 'ir.actions.act_window',
            'res_model': 'pan.mail.account',
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }
        if account:
            action['res_id'] = account.id
        else:
            action['context'] = {
                'default_email': self.email,
                'default_provider': self.x_provider,
                'default_user_id': self.x_owner_user_id.id,
            }
        return action

    def action_test_incoming(self):
        """Test incoming mail configuration by fetching a few messages."""
        self.ensure_one()

        client = self._get_client()

        if not self._has_working_credentials():
            raise UserError(self._no_credentials_error())

        try:
            # Try to fetch 1 message to test connection
            messages = client.fetch_messages(
                account=client.resolve_receiving_account(self),
                mailbox=self,
                folder=FOLDER_INBOX,
                limit=1,
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

        if not self._has_working_credentials():
            raise UserError(self._no_credentials_error())

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

    @api.model_create_multi
    def create(self, vals_list):
        """Creating the first mailbox is what switches Mail Pro on.

        SMTP is taken over here rather than at install time. The module's
        graceful-degradation rule says a database with no mailboxes keeps using
        Odoo's own mail handling — but the install hook used to disable every
        outgoing mail server immediately, so a fresh install could not send the
        user invitations an admin needs *before* Mail Pro is configured. Now the
        takeover happens at the same moment routing does.
        """
        is_first = not self.sudo().with_context(active_test=False).search_count([])
        records = super().create(vals_list)
        if is_first:
            self._activate_smtp_takeover()
        return records

    @api.model
    def _activate_smtp_takeover(self):
        """Disable SMTP so mail cannot leave through two doors at once.

        Idempotent, and safe to call from both the install hook and create().
        """
        IrConfigParameter = self.env['ir.config_parameter'].sudo()
        if IrConfigParameter.get_param('x_pan_outlook_pro.smtp_takeover_done') == 'True':
            return

        MailServer = self.env['ir.mail_server'].sudo().with_context(active_test=False)
        placeholder = self.env.ref(
            'pan_mail_pro.mail_server_invalid_outlook_pro', raise_if_not_found=False
        )

        others = MailServer.search([('active', '=', True)])
        if placeholder:
            others -= placeholder
        if others:
            others.write({'active': False})
            for server in others:
                auth_type = getattr(server, 'smtp_authentication', 'login')
                extra_info = ' (Outlook OAuth)' if auth_type == 'outlook' else ''
                _logger.info(
                    f'[Mail Pro] Disabled SMTP server{extra_info}: {server.name} ({server.smtp_host})'
                )

        if placeholder and not placeholder.active:
            placeholder.write({'active': True})

        IrConfigParameter.set_param('base_setup.default_external_email_server', 'False')
        IrConfigParameter.set_param('x_pan_outlook_pro.smtp_takeover_done', 'True')
        _logger.info('[Mail Pro] SMTP takeover active — all email routes through the provider API')

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

    @api.constrains('x_mailbox_type', 'x_owner_user_id', 'x_sync_mode', 'x_provider')
    def _check_owner_required(self):
        """Ensure an owner is set where the provider actually needs one."""
        for record in self:
            provider = record._get_client().provider_label()
            if record.x_mailbox_type in ('personal', 'notification') and not record.x_owner_user_id:
                raise ValidationError(_(
                    '%(type)s mailbox requires an Owner. '
                    'Please select a user with %(provider)s connected.',
                    type=record.x_mailbox_type.capitalize(), provider=provider,
                ))
            # A shared mailbox needs an owner only where reading it means
            # borrowing a person's delegated token. On Gmail the shared address
            # is its own Workspace account, so there is nobody to borrow from and
            # demanding an owner would make the mailbox unconfigurable.
            if (record.x_mailbox_type == 'shared' and
                    record.x_sync_mode != 'none' and
                    not record.x_owner_user_id and
                    record._get_client().supports_shared_mailbox):
                raise ValidationError(_(
                    'Shared mailbox with sync enabled requires an Owner. '
                    'The Owner\'s %s account will be used to read emails.'
                ) % provider)

    @api.constrains('x_provider', 'x_mailbox_type')
    def _check_provider_supports_mailbox_type(self):
        """Providers differ in what they can service.

        Microsoft 365 has shared mailboxes (send-as with your own token);
        a provider without them must reject that configuration up front rather
        than failing at send time.
        """
        for record in self:
            record._get_client().check_mailbox_supported(record.x_mailbox_type)

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

    @api.constrains('x_sync_mode', 'x_exclude_internal')
    def _check_internal_domains_configured(self):
        """Incoming sync may not be enabled before internal domains exist.

        This is the gate, not the filter. The filter (`should_skip`) used to be
        the only line of defence and it failed open on an empty domain list, so
        a database that was never configured synced every internal email into
        Odoo. Blocking the *configuration* is what makes that unrepeatable; the
        runtime check in `_process_mailbox` only catches a list emptied later.
        """
        gate = self.env['pan.mail.internal.domains'].configuration_error()
        if not gate:
            return
        for record in self:
            if record.x_sync_mode != 'none':
                raise ValidationError(gate)

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
