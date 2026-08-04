# -*- coding: utf-8 -*-
import logging

from odoo import http, _
from odoo.http import request

from ..models.mail_provider_client import (
    get_provider_client,
    get_setup_provider,
    oauth_redirect_uri,
)

_logger = logging.getLogger(__name__)


def _result_page(success, title, message):
    return request.render('pan_mail_pro.oauth_result', {
        'success': success, 'title': title, 'message': message,
    })


class MailProConnectController(http.Controller):
    """One-click entry point for the "connect your mailbox" invitation.

    The invitation email cannot link to a button inside the Odoo client, so it
    links here instead: log in, and land straight on the provider's consent
    screen. A provider without one (IMAP/SMTP, whose credentials are typed in by
    an admin) sends the user to the settings page instead of to a redirect that
    does not exist.
    """

    @http.route('/mail_pro/connect', type='http', auth='user', website=True)
    def connect_mailbox(self, provider=None, **kwargs):
        provider = provider or get_setup_provider(request.env)
        client = provider and get_provider_client(request.env, provider)
        if not client or not client.uses_oauth:
            return request.redirect('/odoo/settings#mail_pro')

        action = request.env.user.action_connect_mailbox(provider)
        return request.redirect(action['url'], local=False)


class MailProOAuthController(http.Controller):
    """Where every provider's consent screen sends the browser back to.

    One implementation, two routes. The paths are fixed strings because they are
    registered in each customer's Azure and Google console - they are public API
    and cannot be derived - but nothing behind them is provider-specific: the
    client exchanges the code, names the address it authorized, and the account
    stores what came back.

    Splitting this in two is what let the two copies drift apart: only Microsoft
    logged which identity had been connected, and only Google kept a refresh
    token Google had declined to re-issue. Neither difference was a decision.
    """

    @http.route('/microsoft_oauth/callback', type='http', auth='user', website=True)
    def microsoft_callback(self, **kwargs):
        return self._handle_callback('outlook', **kwargs)

    @http.route('/google_oauth/callback', type='http', auth='user', website=True)
    def google_callback(self, **kwargs):
        return self._handle_callback('gmail', **kwargs)

    def _handle_callback(self, provider, **kwargs):
        user = request.env.user
        client = get_provider_client(request.env, provider)

        error = kwargs.get('error')
        if error:
            _logger.error('[OAuth] %s returned %s - %s',
                          provider, error, kwargs.get('error_description'))
            return _result_page(False, _('Connection Failed'),
                                f"{error}: {kwargs.get('error_description')}")

        # CSRF: the nonce we handed out must come back, and is good once.
        stored_state = user.sudo().x_pan_mail_oauth_state
        received_state = kwargs.get('state')
        if not received_state or not stored_state or received_state != stored_state:
            _logger.error('[OAuth] CSRF state validation failed for user %s', user.id)
            return _result_page(False, _('Connection Failed'),
                                _('Security validation failed. Please try connecting again.'))
        user.sudo().write({'x_pan_mail_oauth_state': False})

        code = kwargs.get('code')
        if not code:
            _logger.error('[OAuth] No authorization code received from %s', provider)
            return _result_page(False, _('Connection Failed'),
                                _('No authorization code received.'))

        try:
            tokens = client._exchange_code_for_tokens(
                code, oauth_redirect_uri(request.env, provider))
            email = client.get_user_email(tokens['access_token'])

            request.env['pan.mail.account'].sudo()._store_tokens(
                provider, user, email,
                tokens['access_token'], tokens.get('refresh_token'), tokens['token_expiry'],
            )
            _logger.info('[OAuth] Connected %s account %s for Odoo user %s',
                         provider, email, user.login)

            self._retry_error_mailboxes(user, provider)
            self._claim_personal_mailbox(user, provider, email)

            return _result_page(True, _('Mailbox Connected'),
                                _('Your email account has been connected successfully.'))

        except Exception as exception:
            _logger.exception('[OAuth] Failed to handle the %s callback', provider)
            return _result_page(False, _('Connection Failed'), str(exception))

    def _retry_error_mailboxes(self, user, provider):
        """A mailbox that failed for want of a token deserves another go."""
        mailboxes = request.env['x_microsoft.mailbox'].sudo().search([
            ('x_owner_user_id', '=', user.id),
            ('x_provider', '=', provider),
            ('state', '=', 'error'),
        ])
        if mailboxes:
            mailboxes.write({'state': 'draft', 'x_error_message': False})
            _logger.info('[OAuth] Reset %s mailbox(es) from error to draft', len(mailboxes))

    def _claim_personal_mailbox(self, user, provider, email):
        """Give the user the personal mailbox for the address they just authorized.

        Creating it here is what makes "connect" a single click: the address is
        the one the provider just told us about, so there is nothing left to ask.
        An address that already has a mailbox is never repurposed - it may be a
        shared mailbox somebody configured deliberately - only an unowned
        personal one is claimed.
        """
        if not email:
            return

        Mailbox = request.env['x_microsoft.mailbox'].sudo()
        existing = Mailbox.search([('email', '=ilike', email)], limit=1)
        if not existing:
            mailbox = Mailbox.create({
                'email': email,
                'x_provider': provider,
                'x_mailbox_type': 'personal',
                'x_owner_user_id': user.id,
            })
            user.sudo().write({'x_microsoft_default_mailbox_id': mailbox.id})
            _logger.info('[OAuth] Created personal mailbox %s for %s', email, user.login)
        elif existing.x_mailbox_type == 'personal' and not existing.x_owner_user_id:
            existing.write({'x_owner_user_id': user.id})
            _logger.info('[OAuth] Assigned existing mailbox %s to %s', email, user.login)
