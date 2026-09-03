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
from .neutralization import database_is_neutralized

_logger = logging.getLogger(__name__)

# The sync modes that actually import mail. An allow-list, so that a domain
# filtering on it can never accidentally include a mailbox whose mode is unset —
# `!= 'none'` matches NULL in Odoo's ORM, and the answer to "nobody said" has to
# be "do not import".
SYNCING_MODES = ('known_partners', 'all')


class PanMailMailbox(models.Model):
    """An address Mail Pro sends from and, when its sync mode says so, reads.

    Provider-neutral: `provider` names the client that services it, and every
    question a caller has about credentials or capabilities goes through
    `_get_client()` rather than being answered here.
    """
    _name = 'pan.mail.mailbox'
    _description = 'Mailbox'
    _order = 'sequence, email'
    _rec_name = 'email'

    # Every mailbox is serviced by exactly one provider client, resolved
    # through the registry in mail_provider_client.py.
    provider = fields.Selection(
        PROVIDER_SELECTION,
        string='Provider',
        default=DEFAULT_PROVIDER,
        required=True,
        help='Email provider that services this mailbox.',
    )

    def _get_client(self):
        """Return the provider client for this mailbox."""
        self.ensure_one()
        return get_provider_client(self.env, self.provider)

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

        Only personal mailboxes are restricted, and the notification mailbox is
        exempt even though it is one — see the comment on that branch.

        Shared mailboxes are shared on purpose.
        """
        self.ensure_one()
        if not self.active:
            return False
        # The notification mailbox is personal in every other respect — it has
        # an owner and sends with that owner's token — but every internal
        # notification goes out through it from any author, which is the job it
        # exists to do. The hole this method closes is sending as a named
        # colleague, not the company's system address behaving normally.
        if self.mailbox_type == 'personal' and not self.is_notification_mailbox:
            return bool(self.owner_user_id) and self.owner_user_id == user
        return True

    def _syncs_incoming(self):
        """Whether this mailbox imports incoming mail at all.

        Written as an allow-list rather than `!= 'none'` so that an unset value
        — a row predating the field, a NULL the NOT NULL constraint could not be
        applied to — resolves to "does not sync". The unanswered question must
        never resolve to the answer that copies mail into Odoo.
        """
        self.ensure_one()
        return self.sync_mode in SYNCING_MODES

    def _needs_credentials(self):
        """Whether this mailbox needs credentials of its own to do its job.

        A Microsoft shared mailbox that only sends borrows the author's token,
        so it needs none — until it starts reading, which nobody can do on
        somebody else's behalf without being told whose token to use.
        """
        self.ensure_one()
        return self.mailbox_type == 'personal' or self.is_notification_mailbox or self._syncs_incoming()

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
    mailbox_type = fields.Selection([
        ('personal', 'Personal'),
        ('shared', 'Shared'),
    ], string='Type', default='personal', required=True,
        help='Personal: Only the owner can send from this mailbox\n'
             'Shared: All users send from this address; which credentials are used '
             'depends on the provider')

    # Which mailbox sends the system email is a property of a mailbox, not a
    # third kind of mailbox. It used to be a Type value, which forced the
    # question "personal or shared?" to be answered "neither" and made every
    # rule about types carry an exception. A tick box on the one mailbox that
    # does the job says the same thing without the exception.
    # Named for the model it is not: `mail.mail.is_notification` is Odoo's own
    # flag for "this mail is a notification", and the two meet in mail_mail.py.
    is_notification_mailbox = fields.Boolean(
        string='Notification Mailbox',
        default=False,
        help='System emails — user invitations, password resets, activity '
             'reminders — are sent from this mailbox. Exactly one mailbox has '
             'this ticked.',
    )

    owner_user_id = fields.Many2one(
        'res.users',
        string='Owner',
        domain="[('x_pan_mail_connected', '=', True)]",
        help='The user whose credentials this mailbox sends with.',
        index=True
    )

    # -------------------------------------------------------------------------
    # Incoming Mail Configuration
    # -------------------------------------------------------------------------
    # One control, three answers. This used to be a mode plus five booleans
    # computed from it (enabled, enable, include-unknown, inbox, sent), which is
    # six ways to describe one choice and five things that can disagree with it.
    sync_mode = fields.Selection([
        ('none', 'Send only'),
        ('known_partners', 'Send and receive, from existing contacts'),
        ('all', 'Send and receive, from anyone'),
    ], string='Incoming Mail', default='none', required=True,
        help='Whether email arriving in this mailbox is imported into Odoo, '
             'and whether senders who are not contacts yet are imported too.')

    # The interlock that keeps AI auto-routing off. It has no behaviour beyond
    # the constraint below refusing to let it be switched on, which normally
    # makes a field a comment with a database column - but 19.0.4.0.0 made it
    # the explicit gate the AI seam is not allowed to open until real
    # suggestions have earned it. See models/ai/pan_mail_ai.py.
    routing_smart = fields.Boolean(
        string='AI Routing',
        default=False,
        help='Let AI decide where to route (CRM, Helpdesk, etc.)'
    )

    route_to_team = fields.Boolean(
        string='To Team',
        default=False,
        help='Route to a team instead of contact chatter'
    )

    queue_unknown_contacts = fields.Boolean(
        string='Queue for Review',
        default=False,
        help='Hold for manual review instead of auto-creating contacts'
    )

    # Keep for backwards compatibility / internal use
    sync_start_date = fields.Datetime(
        string='Import From',
        default=fields.Datetime.now,
        help='Import emails starting from this date. Default is today.'
    )
    last_sync_date = fields.Datetime(
        string='Last Synced',
        readonly=True,
        help='Timestamp of last successful sync'
    )
    alias_id = fields.Many2one(
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
    error_message = fields.Text(
        string='Last Error',
        readonly=True,
        help='Error message from last failed sync attempt'
    )

    # -------------------------------------------------------------------------
    # Health Status (computed for list view)
    # -------------------------------------------------------------------------
    health_status = fields.Selection([
        ('healthy', 'OK'),
        ('warning', 'Warning'),
        ('error', 'Error'),
    ], string='Status', compute='_compute_health_status', store=False)

    @api.depends('state', 'sync_mode', 'mailbox_type', 'provider', 'owner_user_id',
                 'owner_user_id.x_pan_mail_account_ids.connected')
    def _compute_health_status(self):
        for record in self:
            if record.state == 'error':
                record.health_status = 'error'
            elif record._needs_credentials() and not record._has_working_credentials():
                record.health_status = 'error'
            elif record._syncs_incoming() and record.state == 'draft':
                record.health_status = 'warning'
            else:
                record.health_status = 'healthy'

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

        if self.mailbox_type == 'shared':
            if not client.supports_shared_mailbox:
                # Gmail and IMAP: a shared address is its own account, so there
                # is nothing an owner could connect on its behalf.
                return _(
                    'Shared mailbox "%(email)s" has no credentials of its own. On '
                    '%(provider)s a shared address is its own account, not a '
                    'delegation of someone else\'s.',
                    email=self.email, provider=provider,
                )
            who = sender or self.owner_user_id
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

        if not self.owner_user_id:
            return _(
                'Mailbox "%s" has no Owner. Select the user whose account it '
                'sends and receives with.'
            ) % self.email
        return _(
            'Owner "%(owner)s" has no connected %(provider)s account. '
            'They must connect it first.',
            owner=self.owner_user_id.name, provider=provider,
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
                'default_provider': self.provider,
                'default_user_id': self.owner_user_id.id,
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
                'error_message': False,
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
                'error_message': str(e),
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

        if database_is_neutralized(self.env):
            raise UserError(_(
                'This database is neutralized (a staging or test copy). Syncing '
                'would read live mail and post notifications back out, so it is '
                'refused here.'
            ))

        setup = self.env['pan.mail.setup']
        if not setup.is_ready():
            raise UserError(setup.not_ready_error())

        if not self._syncs_incoming():
            raise UserError(_('Sync mode is set to "No sync". Change it to enable syncing.'))

        if not self._has_working_credentials():
            raise UserError(self._no_credentials_error())

        # Trigger the processor for this mailbox
        processor = self.env['pan.mail.fetcher']
        processor._process_mailbox(self)

        # Mark as active on success (clear any previous error)
        if self.state != 'active':
            self.write({'state': 'active', 'error_message': False})

        # Reload the form to show updated status
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'pan.mail.mailbox',
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
        if IrConfigParameter.get_param('pan_mail_pro.smtp_takeover_done') == 'True':
            return

        MailServer = self.env['ir.mail_server'].sudo().with_context(active_test=False)
        placeholder = self.env.ref(
            'pan_mail_pro.mail_server_disabled', raise_if_not_found=False
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
        IrConfigParameter.set_param('pan_mail_pro.smtp_takeover_done', 'True')
        _logger.info('[Mail Pro] SMTP takeover active — all email routes through the provider API')

    def write(self, vals):
        """Reset last_sync_date when sync_start_date is moved to an earlier date."""
        if 'sync_start_date' in vals and vals['sync_start_date']:
            new_start = fields.Datetime.to_datetime(vals['sync_start_date'])
            for record in self:
                if record.last_sync_date and new_start < record.last_sync_date:
                    vals['last_sync_date'] = new_start
        return super().write(vals)

    @api.onchange('sync_mode')
    def _onchange_sync_mode(self):
        """Reset state when switching to no sync."""
        if not self._syncs_incoming():
            self.state = 'draft'
            self.error_message = False

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

    @api.constrains('mailbox_type', 'is_notification_mailbox', 'owner_user_id', 'sync_mode', 'provider')
    def _check_owner_required(self):
        """Ensure an owner is set where the provider actually needs one."""
        for record in self:
            provider = record._get_client().provider_label()
            if (record.mailbox_type == 'personal' or record.is_notification_mailbox) \
                    and not record.owner_user_id:
                raise ValidationError(_(
                    '%(type)s mailbox requires an Owner. '
                    'Please select a user with %(provider)s connected.',
                    type=_('Notification') if record.is_notification_mailbox
                    else record.mailbox_type.capitalize(),
                    provider=provider,
                ))
            # A shared mailbox needs an owner only where reading it means
            # borrowing a person's delegated token. On Gmail the shared address
            # is its own Workspace account, so there is nobody to borrow from and
            # demanding an owner would make the mailbox unconfigurable.
            if (record.mailbox_type == 'shared' and
                    record._syncs_incoming() and
                    not record.owner_user_id and
                    record._get_client().supports_shared_mailbox):
                raise ValidationError(_(
                    'Shared mailbox with sync enabled requires an Owner. '
                    'The Owner\'s %s account will be used to read emails.'
                ) % provider)

    @api.constrains('provider', 'mailbox_type')
    def _check_provider_supports_mailbox_type(self):
        """Providers differ in what they can service.

        Microsoft 365 has shared mailboxes (send-as with your own token);
        a provider without them must reject that configuration up front rather
        than failing at send time.
        """
        for record in self:
            record._get_client().check_mailbox_supported(record.mailbox_type)

    @api.constrains('is_notification_mailbox', 'owner_user_id', 'provider')
    def _check_notification_owner_is_connected(self):
        """On a consent-screen provider, the notification mailbox's owner has to
        have signed in — it sends with their grant and nothing else.

        This is where "has anybody connected yet?" belongs. It used to be a
        numbered setup step of its own, which asked the question in the
        abstract, in another screen, before there was anything to send. Here it
        is asked of the one mailbox that cannot do its job without an answer, at
        the moment somebody ticks the box.

        Only where the provider uses OAuth. On IMAP the credentials belong to
        the address rather than to a person, so the owner's own connection says
        nothing — asking it there would be the "is this user connected" mistake
        wearing a new hat, and would make the mailbox impossible to create
        before its account exists.

        Only on write of these fields, too: an owner who disconnects later must
        not block every unrelated edit. That case surfaces where it belongs —
        the phase reports `setup` again, because the mailbox can no longer send.
        """
        for record in self:
            if not record.is_notification_mailbox:
                continue
            if not record._get_client().uses_oauth:
                continue
            if not record.owner_user_id.x_pan_mail_connected:
                raise ValidationError(_(
                    'The notification mailbox sends with its owner\'s account, and '
                    '%(owner)s has not connected their mailbox yet. Ask them to open '
                    'the user menu at the top right → My Profile → Mail Pro, or pick '
                    'an owner who has.',
                    owner=record.owner_user_id.name or _('nobody'),
                ))

    @api.constrains('is_notification_mailbox')
    def _check_single_notification_mailbox(self):
        """Ensure only one notification mailbox exists."""
        for record in self:
            if record.is_notification_mailbox:
                existing = self.search([
                    ('is_notification_mailbox', '=', True),
                    ('id', '!=', record.id),
                    ('active', '=', True),
                ], limit=1)
                if existing:
                    raise ValidationError(_(
                        'Only one active Notification mailbox is allowed. '
                        'Existing notification mailbox: %s'
                    ) % existing.email)

    @api.constrains('email', 'sync_mode')
    def _check_internal_domains_configured(self):
        """No mailbox at all before the internal domains exist.

        This is the gate, not the filter. The filter (`should_skip`) used to be
        the only line of defence and it failed open on an empty domain list, so
        a database that was never configured synced every internal email into
        Odoo. Blocking the *configuration* is what makes that unrepeatable; the
        runtime check in `_process_mailbox` only catches a list emptied later.

        It guards every mailbox rather than only the syncing ones, because a
        mailbox is the moment Mail Pro takes over the company's mail: the SMTP
        takeover fires here, sending starts here, and a database that gets this
        far without knowing its own domains is one switch away from copying
        internal mail into Odoo. Gating only the switch left the setting reading
        as an option belonging to sync, which is what it looked like at one
        customer right up until it mattered.
        """
        gate = self.env['pan.mail.domain'].configuration_error()
        if gate:
            raise ValidationError(gate)

    @api.constrains('sync_mode')
    def _check_notification_mailbox_for_sync(self):
        """Ensure notification mailbox exists when enabling incoming sync."""
        for record in self:
            if record._syncs_incoming() and not record.is_notification_mailbox:
                notification_mailbox = self.search([
                    ('is_notification_mailbox', '=', True),
                    ('active', '=', True),
                ], limit=1)
                if not notification_mailbox:
                    raise ValidationError(_(
                        'A Notification mailbox is required for incoming email sync. '
                        'Tick "Notification Mailbox" on the mailbox that should send '
                        'system email first.'
                    ))

    @api.constrains('route_to_team', 'alias_id')
    def _check_alias_required_for_team_routing(self):
        """Ensure alias is set when route_to_team is enabled."""
        for record in self:
            if record.route_to_team and not record.alias_id:
                raise ValidationError(_(
                    'A Team must be selected when "Route to Team" is enabled.'
                ))

    @api.constrains('routing_smart')
    def _check_smart_routing_not_implemented(self):
        """Prevent enabling smart routing until AI routing is implemented."""
        for record in self:
            if record.routing_smart:
                raise ValidationError(_(
                    'Smart AI Routing is not yet implemented. This feature will be available in a future release.'
                ))
