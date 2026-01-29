# -*- coding: utf-8 -*-
import logging
from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class MicrosoftOAuthController(http.Controller):

    @http.route('/microsoft_oauth/callback', type='http', auth='user', website=True)
    def oauth_callback(self, **kwargs):
        """Handle OAuth callback from Microsoft"""

        # Get authorization code and state from query params
        authorization_code = kwargs.get('code')
        received_state = kwargs.get('state')
        error = kwargs.get('error')
        error_description = kwargs.get('error_description')

        if error:
            _logger.error(f"OAuth error: {error} - {error_description}")
            return request.render('pan_outlook_pro.oauth_result', {
                'success': False,
                'title': 'Connection Failed',
                'message': f'{error}: {error_description}',
            })

        # Validate CSRF state parameter
        stored_state = request.env.user.sudo().x_microsoft_oauth_state
        if not received_state or not stored_state or received_state != stored_state:
            _logger.error(f"[OAuth] CSRF state validation failed for user {request.env.user.id}")
            return request.render('pan_outlook_pro.oauth_result', {
                'success': False,
                'title': 'Connection Failed',
                'message': 'Security validation failed. Please try connecting again.',
            })

        # Clear the state token immediately after validation (one-time use)
        request.env.user.sudo().write({'x_microsoft_oauth_state': False})

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

            # Auto-create personal mailbox if setting is enabled
            allow_personal = request.env['ir.config_parameter'].sudo().get_param(
                'x_pan_outlook_pro.allow_personal_mailboxes', 'True'
            )
            if allow_personal and allow_personal.lower() != 'false':
                ms_email = graph_client.get_user_email(token_data['access_token'])
                if ms_email:
                    # Check if mailbox already exists
                    existing = request.env['x_microsoft.mailbox'].sudo().search([
                        ('email', '=ilike', ms_email)
                    ], limit=1)
                    if not existing:
                        mailbox = request.env['x_microsoft.mailbox'].sudo().create({
                            'email': ms_email,
                            'x_mailbox_type': 'personal',
                            'x_owner_user_id': request.env.user.id,
                        })
                        # Set as user's default mailbox
                        request.env.user.sudo().write({
                            'x_microsoft_default_mailbox_id': mailbox.id,
                        })
                        _logger.info(f"[OAuth] Auto-created personal mailbox: {ms_email} for user {request.env.user.login}")
                    elif existing.x_mailbox_type == 'personal' and not existing.x_owner_user_id:
                        # Update existing personal mailbox to set owner if not set
                        existing.sudo().write({'x_owner_user_id': request.env.user.id})
                        _logger.info(f"[OAuth] Updated owner for existing mailbox: {ms_email}")

            # Render minimal page that shows notification and redirects
            return request.render('pan_outlook_pro.oauth_result', {
                'success': True,
                'title': 'Microsoft Connected',
                'message': 'Your Microsoft account has been connected successfully.',
            })

        except Exception as e:
            _logger.exception("Failed to handle OAuth callback")
            return request.render('pan_outlook_pro.oauth_result', {
                'success': False,
                'title': 'Connection Failed',
                'message': str(e),
            })
