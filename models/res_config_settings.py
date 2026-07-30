# -*- coding: utf-8 -*-
import logging
import requests
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from . import encryption_utils
from . import mail_mail
from . import pan_mail_internal_domains as internal_domains
from .mail_provider_client import PROVIDER_SELECTION, get_provider_client  # noqa: F401

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # -------------------------------------------------------------------------
    # Provider selection — step one of setup
    #
    # Which provider an admin is setting up decides every step shown below it:
    # Azure asks for a tenant, Google does not, and showing both credential
    # forms at once is how you get a page that looks broken in either direction.
    # The choices come from the registry, so a newly registered provider shows
    # up here without touching this file.
    #
    # Selecting a provider is a view choice, not an exclusive commitment. Each
    # provider stores its own credentials under its own config parameters, so
    # switching back and forth loses nothing and one database can serve
    # mailboxes on both providers at the same time.
    # -------------------------------------------------------------------------
    x_mail_provider = fields.Selection(
        PROVIDER_SELECTION,
        string='Email Provider',
        config_parameter='x_pan_outlook_pro.setup_provider',
        help='Where your email is hosted. Determines which setup steps are shown.',
    )

    # Provider-neutral state of the selected provider, so the steps below the
    # picker do not each have to know which provider they are looking at.
    x_provider_credentials_set = fields.Boolean(
        compute='_compute_provider_state',
        string='Provider Credentials Set',
    )

    x_provider_connected = fields.Boolean(
        compute='_compute_provider_state',
        string='Provider Account Connected',
    )

    # Module version
    x_microsoft_module_version = fields.Char(
        compute='_compute_module_version',
        string='Module Version'
    )

    # Configuration status
    x_microsoft_config_status = fields.Selection([
        ('not_configured', 'Not Configured'),
        ('configured', 'Configured'),
        ('verified', 'Verified'),
        ('error', 'Error'),
    ], compute='_compute_config_status', string='Configuration Status')

    x_microsoft_config_status_message = fields.Char(
        compute='_compute_config_status',
        string='Status Message'
    )

    # Step status fields for UI
    x_microsoft_mailbox_count = fields.Integer(
        compute='_compute_mailbox_count',
        string='Mailbox Count'
    )

    x_microsoft_notification_configured = fields.Boolean(
        compute='_compute_notification_configured',
        string='Notification Configured'
    )

    # Current user OAuth status (for admin setup flow)
    x_current_user_oauth_connected = fields.Boolean(
        compute='_compute_current_user_oauth_connected',
        string='Current User OAuth Connected'
    )

    x_current_user_google_connected = fields.Boolean(
        compute='_compute_current_user_google_connected',
        string='Current User Google Connected'
    )

    # Internal domain detection uses Odoo's standard mail.alias.domain
    x_microsoft_alias_domains = fields.Char(
        string='Alias Domains',
        compute='_compute_alias_domains',
        help='Internal domains from Odoo mail.alias.domain configuration',
    )

    # -------------------------------------------------------------------------
    # Internal domains
    #
    # The one setting whose absence leaks data, so it is a gate rather than a
    # preference: incoming sync cannot be switched on until it is answered, one
    # way or the other. See pan_mail_internal_domains.py.
    # -------------------------------------------------------------------------
    x_internal_domains = fields.Char(
        string='Internal Domains',
        config_parameter=internal_domains.PARAM_DOMAINS,
        help='Your own email domains, comma separated (e.g. company.com, company.be). '
             'Email from these domains is not synced into Odoo.',
    )

    x_sync_internal_email = fields.Boolean(
        string='Sync Internal Email',
        config_parameter=internal_domains.PARAM_SYNC_INTERNAL,
        help='Turn the internal filter off entirely and sync email between '
             'colleagues into Odoo as well. Everyone with access to a record '
             'can then read that correspondence.',
    )

    x_internal_domains_configured = fields.Boolean(
        compute='_compute_internal_domains_status',
        string='Internal Domains Configured',
    )
    x_internal_domains_suggested = fields.Char(
        compute='_compute_internal_domains_status',
        string='Suggested Domains',
    )
    x_internal_domains_uncovered = fields.Char(
        compute='_compute_internal_domains_status',
        string='Mailbox Domains Not Covered',
    )
    x_internal_sync_mailbox_count = fields.Integer(
        compute='_compute_internal_domains_status',
        string='Mailboxes Syncing Internal Email',
    )

    # Microsoft OAuth Configuration
    x_microsoft_client_id = fields.Char(
        string='Microsoft Client ID',
        help='Application (client) ID from Azure App Registration',
        config_parameter='x_pan_outlook_pro.client_id'
    )

    # Encrypted client secret (hidden, for internal storage only)
    x_microsoft_client_secret_encrypted = fields.Char(
        string='Client Secret (Encrypted)',
        help='Encrypted client secret - stored securely'
    )

    # Computed field for backwards compatibility
    x_microsoft_client_secret = fields.Char(
        string='Client Secret',
        help='Client secret from Azure App Registration',
        compute='_compute_decrypted_client_secret',
        inverse='_inverse_client_secret'
    )

    x_microsoft_tenant_id = fields.Char(
        string='Tenant ID',
        help='Directory (tenant) ID from Azure App Registration',
        config_parameter='x_pan_outlook_pro.tenant_id'
    )

    # Computed redirect URI for display in setup instructions
    x_microsoft_redirect_uri = fields.Char(
        string='Redirect URI',
        compute='_compute_redirect_uri',
        help='The redirect URI to configure in Azure App Registration'
    )

    # -------------------------------------------------------------------------
    # Google OAuth Configuration
    #
    # One credential set per provider, same home as Microsoft's (config params
    # under x_pan_outlook_pro.*). The secret is Fernet-encrypted like Microsoft's.
    # -------------------------------------------------------------------------
    x_google_client_id = fields.Char(
        string='Google Client ID',
        help='OAuth client ID from the Google Cloud Console (Desktop or Web app)',
        config_parameter='x_pan_outlook_pro.google_client_id'
    )

    x_google_client_secret = fields.Char(
        string='Google Client Secret',
        help='OAuth client secret from the Google Cloud Console',
        compute='_compute_decrypted_google_secret',
        inverse='_inverse_google_secret'
    )

    x_google_redirect_uri = fields.Char(
        string='Google Redirect URI',
        compute='_compute_google_redirect_uri',
        help='The redirect URI to configure on the Google OAuth client'
    )

    # OAuth URLs (auto-computed but can be overridden)
    x_microsoft_auth_url = fields.Char(
        string='Authorization URL',
        default='https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize',
        config_parameter='x_pan_outlook_pro.auth_url'
    )
    x_microsoft_token_url = fields.Char(
        string='Token URL',
        default='https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token',
        config_parameter='x_pan_outlook_pro.token_url'
    )

    @api.depends(
        'x_mail_provider',
        'x_microsoft_client_id', 'x_microsoft_client_secret', 'x_microsoft_tenant_id',
        'x_google_client_id', 'x_google_client_secret',
    )
    def _compute_provider_state(self):
        """Answer "is the selected provider set up, and am I connected to it".

        Reads the record's own fields rather than the config parameters so the
        steps below the picker react while the admin is still typing, before
        anything is saved.
        """
        for record in self:
            if record.x_mail_provider == 'outlook':
                record.x_provider_credentials_set = bool(
                    record.x_microsoft_client_id
                    and record.x_microsoft_tenant_id
                    and record.x_microsoft_client_secret
                )
                record.x_provider_connected = record.x_current_user_oauth_connected
            elif record.x_mail_provider == 'gmail':
                record.x_provider_credentials_set = bool(
                    record.x_google_client_id and record.x_google_client_secret
                )
                record.x_provider_connected = record.x_current_user_google_connected
            elif record.x_mail_provider == 'imap':
                # IMAP has no global credential and no consent screen: a login
                # belongs to one address. So "credentials set" means at least
                # one account exists, and "connected" means at least one of them
                # is complete. Both are read from the accounts rather than from
                # fields on this form, which is why they cannot react while the
                # admin types - there is nothing here to type.
                accounts = self.env['pan.mail.account'].sudo().with_context(
                    active_test=False).search([('provider', '=', 'imap')])
                record.x_provider_credentials_set = bool(accounts)
                record.x_provider_connected = any(accounts.mapped('connected'))
            else:
                record.x_provider_credentials_set = False
                record.x_provider_connected = False

    def get_values(self):
        """Pre-select whichever provider already has credentials.

        A database configured before this picker existed has no stored choice.
        Landing it on an empty dropdown would read as "nothing is set up here"
        on an Azure tenant that has been sending mail for months.
        """
        res = super().get_values()
        IrConfigParameter = self.env['ir.config_parameter'].sudo()
        # get_values() runs *after* default_get() has read the config
        # parameters, so anything set here overrides a stored choice. Only fill
        # the gap when there is genuinely nothing stored.
        if not IrConfigParameter.get_param('x_pan_outlook_pro.setup_provider'):
            if IrConfigParameter.get_param('x_pan_outlook_pro.client_id'):
                res['x_mail_provider'] = 'outlook'
            elif IrConfigParameter.get_param('x_pan_outlook_pro.google_client_id'):
                res['x_mail_provider'] = 'gmail'
        return res

    # -------------------------------------------------------------------------
    # Onboarding
    #
    # Setup has a fixed order, and every step used to be discoverable only by
    # knowing it existed. These fields drive a checklist that states what is
    # done, what is next, and what is blocking — the ordering matters most
    # around step 3: connecting your own account before creating notifications@
    # is what turns that mailbox into a single button instead of a form.
    #
    # Which provider is being set up is NOT asked again here. `x_mail_provider`
    # above already answers it, reads its choices from the provider registry
    # (so a new provider appears without touching this file), and exposes
    # `x_provider_credentials_set` / `x_provider_connected` — the two flags
    # steps 2 and 3 need. A second provider picker would have been a duplicate
    # that silently lagged the registry by one provider.
    # -------------------------------------------------------------------------
    x_setup_domains_done = fields.Boolean(compute='_compute_setup_status')
    x_setup_notification_done = fields.Boolean(compute='_compute_setup_status')
    x_setup_users_done = fields.Boolean(compute='_compute_setup_status')
    x_setup_complete = fields.Boolean(compute='_compute_setup_status')
    x_setup_users_total = fields.Integer(compute='_compute_setup_status')
    x_setup_users_connected = fields.Integer(compute='_compute_setup_status')
    x_setup_pending_notifications = fields.Integer(
        compute='_compute_setup_status',
        string='Emails Waiting for Setup',
    )

    x_notification_mailbox_email = fields.Char(
        string='Notification Address',
        default=lambda self: self._default_notification_mailbox_email(),
        help='Address system emails are sent from, e.g. notifications@company.com',
    )

    def _default_notification_mailbox_email(self):
        """Pre-fill notifications@<your domain> so step 5 is one click."""
        Domains = self.env['pan.mail.internal.domains']
        domains = Domains.get_domains() or Domains.suggest_domains()
        return f'notifications@{domains[0]}' if domains else False


    def _compute_module_version(self):
        """Read installed module version from ir.module.module"""
        module = self.env['ir.module.module'].sudo().search([
            ('name', '=', 'pan_mail_pro')
        ], limit=1)
        version = module.installed_version or ''
        for record in self:
            record.x_microsoft_module_version = version

    def _compute_redirect_uri(self):
        """Compute the OAuth redirect URI based on web.base.url"""
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        for record in self:
            record.x_microsoft_redirect_uri = f"{base_url}/microsoft_oauth/callback"

    def _compute_alias_domains(self):
        """Get internal domains from Odoo's standard mail.alias.domain."""
        alias_domains = self.env['mail.alias.domain'].sudo().search([])
        domain_names = ', '.join(alias_domains.mapped('name')) if alias_domains else ''
        for record in self:
            record.x_microsoft_alias_domains = domain_names

    def _compute_decrypted_client_secret(self):
        """Show masked value when encrypted secret exists"""
        for record in self:
            IrConfigParameter = self.env['ir.config_parameter'].sudo()
            encrypted_secret = IrConfigParameter.get_param(
                'x_pan_outlook_pro.client_secret_encrypted'
            )
            # Show masked value if encrypted secret exists, otherwise empty
            record.x_microsoft_client_secret = '********' if encrypted_secret else False

    def _inverse_client_secret(self):
        """Encrypt client secret when writing"""
        for record in self:
            # Skip if the value is the masked placeholder (user didn't change it)
            if record.x_microsoft_client_secret == '********':
                continue

            IrConfigParameter = self.env['ir.config_parameter'].sudo()
            encrypted_secret = encryption_utils.encrypt_value(
                self.env,
                record.x_microsoft_client_secret
            ) if record.x_microsoft_client_secret else False

            IrConfigParameter.set_param(
                'x_pan_outlook_pro.client_secret_encrypted',
                encrypted_secret or ''
            )

    def _compute_google_redirect_uri(self):
        """Compute the Google OAuth redirect URI based on web.base.url"""
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        for record in self:
            record.x_google_redirect_uri = f"{base_url}/google_oauth/callback"

    def _compute_decrypted_google_secret(self):
        """Show a masked value when an encrypted Google secret exists."""
        IrConfigParameter = self.env['ir.config_parameter'].sudo()
        for record in self:
            encrypted_secret = IrConfigParameter.get_param(
                'x_pan_outlook_pro.google_client_secret_encrypted'
            )
            record.x_google_client_secret = '********' if encrypted_secret else False

    def _inverse_google_secret(self):
        """Encrypt the Google client secret when writing."""
        IrConfigParameter = self.env['ir.config_parameter'].sudo()
        for record in self:
            # Masked placeholder means the user didn't touch it.
            if record.x_google_client_secret == '********':
                continue
            encrypted_secret = encryption_utils.encrypt_value(
                self.env, record.x_google_client_secret
            ) if record.x_google_client_secret else False
            IrConfigParameter.set_param(
                'x_pan_outlook_pro.google_client_secret_encrypted',
                encrypted_secret or ''
            )

    def _compute_config_status(self):
        """Compute the Azure configuration status"""
        IrConfigParameter = self.env['ir.config_parameter'].sudo()
        last_test_result = IrConfigParameter.get_param('x_pan_outlook_pro.config_test_result', '')
        last_test_message = IrConfigParameter.get_param('x_pan_outlook_pro.config_test_message', '')

        for record in self:
            client_id = record.x_microsoft_client_id
            tenant_id = record.x_microsoft_tenant_id
            encrypted_secret = IrConfigParameter.get_param('x_pan_outlook_pro.client_secret_encrypted')

            if not client_id or not tenant_id or not encrypted_secret:
                record.x_microsoft_config_status = 'not_configured'
                record.x_microsoft_config_status_message = _('Please fill in all Azure credentials')
            elif last_test_result == 'verified':
                record.x_microsoft_config_status = 'verified'
                record.x_microsoft_config_status_message = last_test_message or _('Configuration verified')
            elif last_test_result == 'error':
                record.x_microsoft_config_status = 'error'
                record.x_microsoft_config_status_message = last_test_message or _('Configuration error')
            else:
                record.x_microsoft_config_status = 'configured'
                record.x_microsoft_config_status_message = _('Click "Test Configuration" to verify')

    def _compute_mailbox_count(self):
        """Compute the number of configured mailboxes"""
        mailbox_count = self.env['x_microsoft.mailbox'].sudo().search_count([])
        for record in self:
            record.x_microsoft_mailbox_count = mailbox_count

    def _compute_notification_configured(self):
        """Check if a notification mailbox is configured *and usable*.

        Having the record is not the same as being able to send from it: an
        owner whose OAuth expired leaves a mailbox that looks configured and
        silently queues every notification.
        """
        notification_mailbox = self.env['x_microsoft.mailbox'].sudo().search([
            ('x_mailbox_type', '=', 'notification'),
            ('active', '=', True),
        ], limit=1)
        usable = bool(notification_mailbox) and notification_mailbox._has_working_credentials()

        for record in self:
            record.x_microsoft_notification_configured = usable

    def _mail_pro_users(self):
        """Internal users who are expected to connect a mailbox.

        OdooBot is excluded: it is an internal user that will never authorize
        anything, and counting it would leave step 6 permanently at "almost".
        """
        domain = [('share', '=', False), ('active', '=', True)]
        odoobot = self.env.ref('base.user_root', raise_if_not_found=False)
        if odoobot:
            domain.append(('id', '!=', odoobot.id))
        return self.env['res.users'].sudo().search(domain)

    @api.depends('x_internal_domains')
    def _compute_internal_domains_status(self):
        Domains = self.env['pan.mail.internal.domains']
        suggested = ', '.join(Domains.suggest_domains())
        uncovered = ', '.join(Domains.uncovered_mailbox_domains())
        internal_sync_count = self.env['x_microsoft.mailbox'].sudo().search_count([
            ('x_exclude_internal', '=', False),
            ('x_sync_mode', '!=', 'none'),
        ])
        for record in self:
            # Read the form's value, not the saved parameter: the admin may be
            # typing domains right now and the warnings should follow along.
            configured = bool(Domains._parse(record.x_internal_domains))
            record.x_internal_domains_configured = configured
            record.x_internal_domains_suggested = suggested
            record.x_internal_domains_uncovered = uncovered if configured else ''
            record.x_internal_sync_mailbox_count = internal_sync_count

    @api.depends('x_mail_provider', 'x_provider_credentials_set', 'x_provider_connected',
                 'x_internal_domains', 'x_sync_internal_email')
    def _compute_setup_status(self):
        """The checklist steps that are not about the provider.

        Steps 1-3 (pick a provider, its credentials, connect your account) are
        `x_mail_provider` plus `_compute_provider_state`. Everything from step 4
        on is provider-independent — internal domains, the notification mailbox,
        the users — so it lives here and never asks which provider it is.

        Each step answers "can the next one succeed", not "did somebody fill in
        a field": a notification mailbox whose owner's token expired is not a
        done step.
        """
        Mailbox = self.env['x_microsoft.mailbox'].sudo()
        Domains = self.env['pan.mail.internal.domains']

        users = self._mail_pro_users()
        users_connected = len(users.filtered('x_pan_mail_connected'))

        pending = self.env['mail.mail'].sudo().search_count([
            ('state', '=', 'outgoing'),
            ('failure_reason', '=', mail_mail.NOTIFICATION_PENDING_REASON),
        ])

        notification_ok = bool(Mailbox.search([
            ('x_mailbox_type', '=', 'notification'), ('active', '=', True),
        ], limit=1).filtered(lambda m: m._has_working_credentials()))

        for record in self:
            record.x_setup_domains_done = (
                bool(Domains._parse(record.x_internal_domains)) or record.x_sync_internal_email
            )
            record.x_setup_notification_done = notification_ok
            record.x_setup_users_total = len(users)
            record.x_setup_users_connected = users_connected
            record.x_setup_users_done = bool(users) and users_connected == len(users)
            record.x_setup_pending_notifications = pending
            record.x_setup_complete = bool(
                record.x_mail_provider
                and record.x_provider_credentials_set
                and record.x_provider_connected
                and record.x_setup_domains_done
                and notification_ok
            )

    def action_apply_suggested_internal_domains(self):
        """Fill the domain list with everything we can derive from the database.

        The admin still has to save, so this is a suggestion they confirm rather
        than a setting that appears behind their back.
        """
        self.ensure_one()
        self.x_internal_domains = self.x_internal_domains_suggested
        # Returning nothing on purpose: the client re-reads this same transient
        # record, so the filled-in field survives. Re-opening the settings action
        # would build a fresh record from the saved parameters and throw it away.
        return None

    def action_create_notification_mailbox(self):
        """Create notifications@ in one click, owned by whoever is setting up.

        Step 3 (connect your own account) comes first precisely so this button
        has a connected owner to point at — that is what breaks the
        chicken-and-egg where you need a working notification mailbox to invite
        the users whose accounts you need.
        """
        self.ensure_one()
        email = (self.x_notification_mailbox_email or '').strip()
        if not email:
            raise UserError(_('Please enter the address system emails should be sent from.'))

        Mailbox = self.env['x_microsoft.mailbox']
        existing = Mailbox.with_context(active_test=False).search([
            ('x_mailbox_type', '=', 'notification'),
        ], limit=1)
        if existing:
            raise UserError(_(
                'A Notification mailbox already exists (%s). Edit that one instead.'
            ) % existing.email)

        if not self.x_mail_provider:
            raise UserError(_('Choose your email provider first.'))

        # The mailbox is served by the provider being set up, whatever that is —
        # reading the picker rather than mapping known provider codes means a
        # newly registered provider works here without an edit.
        if not self.env.user.x_pan_mail_connected:
            raise UserError(_(
                'Connect your own email account first — the notification mailbox '
                'sends with its owner\'s credentials.'
            ))

        mailbox = Mailbox.create({
            'email': email,
            'x_mailbox_type': 'notification',
            'x_provider': self.x_mail_provider,
            'x_owner_user_id': self.env.user.id,
        })
        _logger.info(f"[Mail Pro] Created notification mailbox {mailbox.email} from setup checklist")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Notification Mailbox Created'),
                'message': _('System emails are now sent from %s.') % mailbox.email,
                'type': 'success',
                'sticky': False,
                # Stay on the settings page — navigating to the mailbox form
                # would discard whatever else the admin has typed.
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def action_open_unconnected_users(self):
        """Show exactly who still has to connect their mailbox."""
        self.ensure_one()
        pending = self._mail_pro_users().filtered(
            lambda u: not u.x_pan_mail_connected
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('Users Without a Connected Mailbox'),
            'res_model': 'res.users',
            'view_mode': 'list,form',
            'domain': [('id', 'in', pending.ids)],
            'target': 'current',
        }

    def action_send_connect_invites(self):
        """Ask every user who has not connected yet to do so."""
        self.ensure_one()
        pending = self._mail_pro_users().filtered(
            lambda u: not u.x_pan_mail_connected
        )
        sent = pending._send_connect_invites()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Invitations Sent'),
                'message': _('Asked %d user(s) to connect their mailbox.') % sent,
                'type': 'success',
                'sticky': False,
            },
        }

    def _compute_current_user_oauth_connected(self):
        """Check if the current user has Microsoft OAuth connected"""
        for record in self:
            record.x_current_user_oauth_connected = self.env.user.x_microsoft_oauth_connected

    def _compute_current_user_google_connected(self):
        """Check if the current user has a Google account connected"""
        for record in self:
            record.x_current_user_google_connected = self.env.user.x_google_oauth_connected

    def action_connect_google_admin(self):
        """Start the Google OAuth flow from the settings page."""
        self.ensure_one()
        return self.env.user.action_connect_google()

    def action_connect_microsoft_admin(self):
        """Start OAuth flow by redirecting directly to Microsoft login"""
        self.ensure_one()

        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        redirect_uri = f"{base_url}/microsoft_oauth/callback"

        graph_client = get_provider_client(self.env)

        # Generate and store CSRF state token for current user
        state = graph_client.generate_oauth_state()
        self.env.user.sudo().write({'x_microsoft_oauth_state': state})

        auth_url = graph_client.get_authorization_url(redirect_uri, state=state)

        return {
            'type': 'ir.actions.act_url',
            'url': auth_url,
            'target': 'new',
        }

    def action_test_azure_configuration(self):
        """
        Test the Azure App configuration by validating the tenant ID.

        Note: Client ID and Client Secret are validated when a user connects
        their Microsoft account (OAuth flow). This follows the principle of
        least privilege - we don't use application permissions.
        """
        self.ensure_one()
        IrConfigParameter = self.env['ir.config_parameter'].sudo()

        # Get configuration values
        client_id = self.x_microsoft_client_id
        tenant_id = self.x_microsoft_tenant_id
        encrypted_secret = IrConfigParameter.get_param('x_pan_outlook_pro.client_secret_encrypted')

        # Validate all fields are filled
        if not client_id or not tenant_id or not encrypted_secret:
            raise UserError(_('Please fill in Client ID, Client Secret, and Tenant ID before testing.'))

        # Decrypt client secret to verify it's valid
        client_secret = encryption_utils.decrypt_value(self.env, encrypted_secret)
        if not client_secret:
            raise UserError(_('Client secret could not be decrypted. Please re-enter it.'))

        # Verify tenant exists by checking OpenID configuration
        try:
            openid_url = f'https://login.microsoftonline.com/{tenant_id}/.well-known/openid-configuration'
            response = requests.get(openid_url, timeout=10)
            if response.status_code != 200:
                self._save_test_result('error', _('Invalid Tenant ID - tenant not found'))
                raise UserError(_('Invalid Tenant ID. The tenant "%s" was not found.') % tenant_id)

            # Verify we got valid OpenID configuration
            openid_config = response.json()
            if 'authorization_endpoint' not in openid_config:
                self._save_test_result('error', _('Invalid OpenID configuration'))
                raise UserError(_('Invalid response from Microsoft. Please check the Tenant ID.'))

            # Success - tenant is valid, credentials will be validated during OAuth
            self._save_test_result(
                'verified',
                _('Tenant ID verified. Client credentials will be validated when a user connects their Microsoft account.')
            )

            return {
                'type': 'ir.actions.client',
                'tag': 'reload',
            }

        except requests.exceptions.RequestException as e:
            self._save_test_result('error', _('Network error: %s') % str(e))
            raise UserError(_('Could not connect to Microsoft: %s') % str(e))

    def _save_test_result(self, status, message):
        """Save the test result to ir.config_parameter for display"""
        IrConfigParameter = self.env['ir.config_parameter'].sudo()
        IrConfigParameter.set_param('x_pan_outlook_pro.config_test_result', status)
        IrConfigParameter.set_param('x_pan_outlook_pro.config_test_message', message)
        _logger.info(f"[Graph API] Config test result: {status} - {message}")

