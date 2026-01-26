# -*- coding: utf-8 -*-
import logging
import requests
from odoo import fields, models, api, _
from odoo.exceptions import UserError
from . import encryption_utils

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

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

    # Microsoft OAuth Configuration
    x_microsoft_client_id = fields.Char(
        string='Microsoft Client ID',
        help='Application (client) ID from Azure App Registration',
        config_parameter='x_pan_outlook_pro.client_id'
    )

    # Encrypted client secret (hidden, for internal storage only)
    x_microsoft_client_secret_encrypted = fields.Char(
        string='Microsoft Client Secret (Encrypted)',
        help='Encrypted client secret - stored securely'
    )

    # Computed field for backwards compatibility
    x_microsoft_client_secret = fields.Char(
        string='Microsoft Client Secret',
        help='Client secret from Azure App Registration',
        compute='_compute_decrypted_client_secret',
        inverse='_inverse_client_secret'
    )

    x_microsoft_tenant_id = fields.Char(
        string='Microsoft Tenant ID',
        help='Directory (tenant) ID from Azure App Registration',
        config_parameter='x_pan_outlook_pro.tenant_id'
    )

    # Computed redirect URI for display in setup instructions
    x_microsoft_redirect_uri = fields.Char(
        string='Redirect URI',
        compute='_compute_redirect_uri',
        help='The redirect URI to configure in Azure App Registration'
    )

    # System Notification Settings
    # Used for activity reminders, mentions, and other system-generated emails
    x_microsoft_notification_mailbox_id = fields.Many2one(
        'x_microsoft.mailbox',
        string='Notification Mailbox',
        help='Mailbox used for system notifications (activity reminders, mentions, etc.)'
    )
    x_microsoft_notification_user_id = fields.Many2one(
        'res.users',
        string='Notification Sender',
        domain="[('x_microsoft_oauth_connected', '=', True)]",
        help='User whose Microsoft account sends system notifications. '
             'Must have SendAs permission for the notification mailbox.'
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

    def _compute_redirect_uri(self):
        """Compute the OAuth redirect URI based on web.base.url"""
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        for record in self:
            record.x_microsoft_redirect_uri = f"{base_url}/microsoft_oauth/callback"

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

    @api.model
    def get_values(self):
        """Load notification settings from ir.config_parameter"""
        res = super().get_values()
        IrConfigParameter = self.env['ir.config_parameter'].sudo()

        # Load notification mailbox
        mailbox_id = IrConfigParameter.get_param('x_pan_outlook_pro.notification_mailbox_id')
        if mailbox_id:
            res['x_microsoft_notification_mailbox_id'] = int(mailbox_id)

        # Load notification user
        user_id = IrConfigParameter.get_param('x_pan_outlook_pro.notification_user_id')
        if user_id:
            res['x_microsoft_notification_user_id'] = int(user_id)

        return res

    def set_values(self):
        """Save notification settings to ir.config_parameter"""
        super().set_values()
        IrConfigParameter = self.env['ir.config_parameter'].sudo()

        # Save notification mailbox
        IrConfigParameter.set_param(
            'x_pan_outlook_pro.notification_mailbox_id',
            self.x_microsoft_notification_mailbox_id.id if self.x_microsoft_notification_mailbox_id else ''
        )

        # Save notification user
        IrConfigParameter.set_param(
            'x_pan_outlook_pro.notification_user_id',
            self.x_microsoft_notification_user_id.id if self.x_microsoft_notification_user_id else ''
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
        """Check if notification settings are fully configured"""
        IrConfigParameter = self.env['ir.config_parameter'].sudo()
        mailbox_id = IrConfigParameter.get_param('x_pan_outlook_pro.notification_mailbox_id')
        user_id = IrConfigParameter.get_param('x_pan_outlook_pro.notification_user_id')

        for record in self:
            record.x_microsoft_notification_configured = bool(mailbox_id and user_id)

    def action_test_azure_configuration(self):
        """
        Test the Azure App configuration by validating the tenant
        and attempting to get a token using client credentials.
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

        # Decrypt client secret
        client_secret = encryption_utils.decrypt_value(self.env, encrypted_secret)
        if not client_secret:
            raise UserError(_('Client secret could not be decrypted. Please re-enter it.'))

        # Step 1: Verify tenant exists by checking OpenID configuration
        try:
            openid_url = f'https://login.microsoftonline.com/{tenant_id}/.well-known/openid-configuration'
            response = requests.get(openid_url, timeout=10)
            if response.status_code != 200:
                self._save_test_result('error', _('Invalid Tenant ID - tenant not found'))
                raise UserError(_('Invalid Tenant ID. The tenant "%s" was not found.') % tenant_id)
        except requests.exceptions.RequestException as e:
            self._save_test_result('error', _('Network error: %s') % str(e))
            raise UserError(_('Could not connect to Microsoft: %s') % str(e))

        # Step 2: Try to get a token using client credentials flow
        # This validates client_id and client_secret
        token_url = f'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token'
        token_data = {
            'grant_type': 'client_credentials',
            'client_id': client_id,
            'client_secret': client_secret,
            'scope': 'https://graph.microsoft.com/.default',
        }

        try:
            response = requests.post(token_url, data=token_data, timeout=10)
            result = response.json()

            if response.status_code == 200 and 'access_token' in result:
                # Success! Save result and reload the settings page
                self._save_test_result('verified', _('Azure configuration verified successfully'))

                # Return action to reload settings with success notification
                return {
                    'type': 'ir.actions.client',
                    'tag': 'reload',
                }
            else:
                # Token request failed
                error = result.get('error', 'unknown_error')
                error_desc = result.get('error_description', 'Unknown error')

                if 'invalid_client' in error or 'AADSTS7000215' in error_desc:
                    msg = _('Invalid Client Secret. Please check and re-enter it.')
                elif 'unauthorized_client' in error or 'AADSTS700016' in error_desc:
                    msg = _('Invalid Client ID. Application not found in tenant.')
                else:
                    msg = _('Authentication failed: %s') % error_desc

                self._save_test_result('error', msg)
                raise UserError(msg)

        except requests.exceptions.RequestException as e:
            self._save_test_result('error', _('Network error: %s') % str(e))
            raise UserError(_('Could not connect to Microsoft: %s') % str(e))

    def _save_test_result(self, status, message):
        """Save the test result to ir.config_parameter for display"""
        IrConfigParameter = self.env['ir.config_parameter'].sudo()
        IrConfigParameter.set_param('x_pan_outlook_pro.config_test_result', status)
        IrConfigParameter.set_param('x_pan_outlook_pro.config_test_message', message)
        _logger.info(f"[Outlook Pro] Config test result: {status} - {message}")
