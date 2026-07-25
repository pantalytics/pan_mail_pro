# -*- coding: utf-8 -*-
from odoo import models, fields, _

from ..models.mail_provider_client import get_provider_client


class MicrosoftOAuthWizard(models.TransientModel):
    _name = 'microsoft.oauth.wizard'
    _description = 'Microsoft OAuth Connection Wizard'

    state = fields.Selection([
        ('start', 'Start'),
        ('success', 'Success'),
        ('test', 'Test Connection'),
    ], default='start', string='State')

    # Success state fields
    user_display_name = fields.Char(string='User Name', readonly=True)
    user_email = fields.Char(string='User Email', readonly=True)

    # Test connection result
    test_success = fields.Boolean(string='Test Success', readonly=True)
    test_message = fields.Text(string='Test Result', readonly=True)

    def action_connect_microsoft(self):
        """Start OAuth flow by redirecting to Microsoft login"""
        self.ensure_one()

        # Generate redirect URI (Odoo will handle the callback)
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        redirect_uri = f"{base_url}/microsoft_oauth/callback"

        # Get authorization URL with CSRF state token
        graph_client = get_provider_client(self.env)

        # Generate and store CSRF state token for current user
        state = graph_client.generate_oauth_state()
        self.env.user.sudo().write({'x_microsoft_oauth_state': state})

        auth_url = graph_client.get_authorization_url(redirect_uri, state=state)

        # Redirect to Microsoft login (same window)
        return {
            'type': 'ir.actions.act_url',
            'url': auth_url,
            'target': 'self',
        }

    def action_test_connection(self):
        """Test the Graph API connection"""
        self.ensure_one()

        graph_client = get_provider_client(self.env)
        result = graph_client.test_connection(self.env.user)

        if result['success']:
            self.write({
                'state': 'test',
                'test_success': True,
                'test_message': f"✓ Connected successfully!\n\nDisplay Name: {result['display_name']}\nEmail: {result['email']}\nUser ID: {result['id']}",
                'user_display_name': result['display_name'],
                'user_email': result['email'],
            })
        else:
            self.write({
                'state': 'test',
                'test_success': False,
                'test_message': f"✗ Connection failed:\n\n{result['error']}",
            })

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'microsoft.oauth.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_disconnect(self):
        """Disconnect Microsoft account"""
        self.ensure_one()

        # Use sudo() because token fields have groups='base.group_system'
        self.env.user.sudo().write({
            'x_microsoft_access_token': False,
            'x_microsoft_refresh_token': False,
            'x_microsoft_token_expiry': False,
            'x_microsoft_default_mailbox_id': False,
            'x_microsoft_oauth_state': False,
        })

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Disconnected'),
                'message': _('Microsoft account has been disconnected.'),
                'type': 'success',
                'sticky': False,
            }
        }

    def action_close(self):
        """Close the wizard"""
        return {'type': 'ir.actions.act_window_close'}
