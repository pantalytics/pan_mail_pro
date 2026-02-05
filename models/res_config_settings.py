# -*- coding: utf-8 -*-
import logging
import requests
from odoo import fields, models, api, _
from odoo.exceptions import UserError
from . import encryption_utils

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

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

    # Internal domain detection uses Odoo's standard mail.alias.domain
    x_microsoft_alias_domains = fields.Char(
        string='Alias Domains',
        compute='_compute_alias_domains',
        help='Internal domains from Odoo mail.alias.domain configuration',
    )

    # Microsoft OAuth Configuration
    x_microsoft_client_id = fields.Char(
        string='Client ID',
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

    def _compute_module_version(self):
        """Read installed module version from ir.module.module"""
        module = self.env['ir.module.module'].sudo().search([
            ('name', '=', 'pan_outlook_pro')
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
        """Check if a notification mailbox is configured"""
        notification_mailbox = self.env['x_microsoft.mailbox'].sudo().search([
            ('x_mailbox_type', '=', 'notification'),
            ('active', '=', True),
            ('x_owner_user_id', '!=', False),
        ], limit=1)

        for record in self:
            record.x_microsoft_notification_configured = bool(notification_mailbox)

    def _compute_current_user_oauth_connected(self):
        """Check if the current user has Microsoft OAuth connected"""
        for record in self:
            record.x_current_user_oauth_connected = self.env.user.x_microsoft_oauth_connected

    def action_connect_microsoft_admin(self):
        """Start OAuth flow by redirecting directly to Microsoft login"""
        self.ensure_one()

        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        redirect_uri = f"{base_url}/microsoft_oauth/callback"

        graph_client = self.env['microsoft.graph.client']

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

