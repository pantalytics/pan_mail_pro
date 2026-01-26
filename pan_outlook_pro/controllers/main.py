# -*- coding: utf-8 -*-
import logging
from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class MicrosoftOAuthController(http.Controller):

    @http.route('/microsoft_oauth/callback', type='http', auth='user', website=True)
    def oauth_callback(self, **kwargs):
        """Handle OAuth callback from Microsoft"""

        # Get authorization code from query params
        authorization_code = kwargs.get('code')
        error = kwargs.get('error')
        error_description = kwargs.get('error_description')

        if error:
            _logger.error(f"OAuth error: {error} - {error_description}")
            return request.render('pan_outlook_pro.oauth_result', {
                'success': False,
                'title': 'Connection Failed',
                'message': f'{error}: {error_description}',
            })

        if not authorization_code:
            _logger.error("No authorization code received")
            return request.render('pan_outlook_pro.oauth_result', {
                'success': False,
                'title': 'Connection Failed',
                'message': 'No authorization code received from Microsoft',
            })

        try:
            # Exchange code for tokens
            base_url = request.env['ir.config_parameter'].sudo().get_param('web.base.url')
            redirect_uri = f"{base_url}/microsoft_oauth/callback"

            graph_client = request.env['microsoft.graph.client']
            token_data = graph_client.exchange_code_for_tokens(authorization_code, redirect_uri)

            # Save tokens to current user
            # Use sudo() because token fields have groups='base.group_system'
            # This is safe because we only write to the current user's own record
            request.env.user.sudo().write({
                'x_microsoft_access_token': token_data['access_token'],
                'x_microsoft_refresh_token': token_data['refresh_token'],
                'x_microsoft_token_expiry': token_data['token_expiry'],
            })

            # Log which Microsoft identity was connected
            ms_identity = graph_client._get_token_identity(token_data['access_token'])
            _logger.info(f"[OAuth] Connected Microsoft account for Odoo user: {request.env.user.name} (ID: {request.env.user.id})")
            _logger.info(f"[OAuth] Microsoft identity connected: {ms_identity}")

            # Render minimal page that shows notification and redirects
            return request.render('pan_outlook_pro.oauth_result', {
                'success': True,
                'title': 'Microsoft Connected',
                'message': 'Your Microsoft account has been connected. Please select a default mailbox.',
            })

        except Exception as e:
            _logger.exception("Failed to handle OAuth callback")
            return request.render('pan_outlook_pro.oauth_result', {
                'success': False,
                'title': 'Connection Failed',
                'message': str(e),
            })
