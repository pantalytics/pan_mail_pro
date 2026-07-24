# -*- coding: utf-8 -*-
"""Gmail REST API client — the Google counterpart of microsoft/graph_client.py.

Same shape, different wire. Raw `requests` against googleapis.com, no
google-api-python-client dependency, so it installs like the rest of the module.
This file is the ONLY place Gmail's JSON and OAuth details are understood; the
provider normalizes on top of it and no caller above sees a Gmail key.

Why REST and not IMAP: the decision (2026-07-24) chose the native Gmail API over
IMAP/SMTP. The history API replaces IMAP's UIDVALIDITY/UIDNEXT state machine, and
draft/send hands back a real Message-ID and threadId for threading and dedup —
the same seam the Graph client already gives us.
"""
import logging
import requests
import secrets
from datetime import datetime, timedelta

from odoo import models, api, _
from odoo.exceptions import UserError
from ... import encryption_utils

_logger = logging.getLogger(__name__)

# Google OAuth 2.0 endpoints (stable, not per-tenant like Microsoft's).
GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'

# Restricted scopes. gmail.modify covers reading + labelling incoming, gmail.send
# covers sending. These are what an "Internal" Workspace app skips CASA for; a
# public app would need the security assessment. openid/email identify the user
# during the callback.
GOOGLE_SCOPES = [
    'openid',
    'email',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/gmail.send',
]


class GmailClient(models.AbstractModel):
    """Helper model for Gmail REST API calls."""
    _name = 'gmail.client'
    _description = 'Gmail REST API Client'

    # -------------------------------------------------------------------------
    # Configuration
    # -------------------------------------------------------------------------
    @api.model
    def _get_config_params(self):
        """Read Google OAuth configuration from settings.

        Credentials are one set per provider (config params), mirroring how
        Microsoft's live under x_pan_outlook_pro.* — the credential home decided
        in Phase 2. The secret is Fernet-encrypted at rest like Microsoft's.
        """
        ICP = self.env['ir.config_parameter'].sudo()
        encrypted_secret = ICP.get_param('x_pan_outlook_pro.google_client_secret_encrypted')
        client_secret = encryption_utils.decrypt_value(
            self.env, encrypted_secret
        ) if encrypted_secret else False

        return {
            'client_id': ICP.get_param('x_pan_outlook_pro.google_client_id'),
            'client_secret': client_secret,
        }

    @api.model
    def generate_oauth_state(self):
        """Cryptographically secure state token for CSRF protection."""
        return secrets.token_urlsafe(32)

    @api.model
    def get_authorization_url(self, redirect_uri, state=None):
        """Build the Google consent URL.

        access_type=offline + prompt=consent is what makes Google return a
        refresh token; without them a re-authorizing user gets an access token
        only and the account silently stops working after an hour.
        """
        config = self._get_config_params()
        client_id = config['client_id']
        if not client_id:
            raise UserError(_('Please configure the Google Client ID in Settings.'))

        params = {
            'client_id': client_id,
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'scope': ' '.join(GOOGLE_SCOPES),
            'access_type': 'offline',
            'prompt': 'consent',
        }
        if state:
            params['state'] = state

        query = '&'.join(f'{k}={requests.utils.quote(str(v))}' for k, v in params.items())
        return f'{GOOGLE_AUTH_URL}?{query}'

    # -------------------------------------------------------------------------
    # Token lifecycle
    # -------------------------------------------------------------------------
    @api.model
    def exchange_code_for_tokens(self, authorization_code, redirect_uri):
        """Trade an authorization code for access + refresh tokens."""
        config = self._get_config_params()
        data = {
            'client_id': config['client_id'],
            'client_secret': config['client_secret'],
            'code': authorization_code,
            'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code',
        }
        try:
            response = requests.post(GOOGLE_TOKEN_URL, data=data, timeout=10)
            response.raise_for_status()
            token_data = response.json()
            expires_in = token_data.get('expires_in', 3600)
            return {
                'access_token': token_data.get('access_token'),
                'refresh_token': token_data.get('refresh_token'),
                'token_expiry': datetime.now() + timedelta(seconds=expires_in),
            }
        except requests.exceptions.RequestException as e:
            raise UserError(_('Failed to authenticate with Google: %s') % self._error_detail(e))

    @api.model
    def refresh_access_token(self, account):
        """Refresh the access token for `account`.

        Google does NOT return a new refresh token on refresh, so the existing
        one is preserved — dropping it would disconnect the account on the next
        cycle. Same fallback the Microsoft client uses.
        """
        if not account.refresh_token:
            raise UserError(_('No refresh token available. Please reconnect your Google account.'))

        config = self._get_config_params()
        data = {
            'client_id': config['client_id'],
            'client_secret': config['client_secret'],
            'refresh_token': account.refresh_token,
            'grant_type': 'refresh_token',
        }
        try:
            response = requests.post(GOOGLE_TOKEN_URL, data=data, timeout=10)
            response.raise_for_status()
            token_data = response.json()
            expires_in = token_data.get('expires_in', 3600)
            account.sudo().write({
                'access_token': token_data.get('access_token'),
                'refresh_token': token_data.get('refresh_token') or account.refresh_token,
                'token_expiry': datetime.now() + timedelta(seconds=expires_in),
            })
            return token_data.get('access_token')
        except requests.exceptions.RequestException as e:
            error_code = self._error_code(e)
            # invalid_grant: refresh token revoked, expired, or consent withdrawn.
            if error_code == 'invalid_grant':
                _logger.warning('[Gmail API] Permanent token failure for %s, clearing tokens', account.email)
                account.sudo().write({
                    'access_token_encrypted': False,
                    'refresh_token_encrypted': False,
                    'token_expiry': False,
                })
                raise UserError(_(
                    'Your Google connection has expired or been revoked. '
                    'Please reconnect your Google account.'
                ))
            raise UserError(_('Failed to refresh Google token: %s') % self._error_detail(e))

    @api.model
    def get_valid_token(self, account):
        """Return a live access token for `account`, refreshing if near expiry."""
        if account.token_expiry:
            if account.token_expiry <= datetime.now() + timedelta(minutes=5):
                _logger.info('[Gmail API] Token expired for %s, refreshing...', account.email)
                return self.refresh_access_token(account)

        if not account.access_token:
            raise UserError(_('No access token available. Please connect your Google account.'))
        return account.access_token

    # -------------------------------------------------------------------------
    # Identity
    # -------------------------------------------------------------------------
    @api.model
    def get_user_email(self, access_token):
        """Return the authenticated account's own address.

        Used right after the OAuth exchange to auto-create the personal mailbox,
        the same way the Graph client does. The Gmail profile endpoint is covered
        by the gmail.modify scope we already hold, so no extra consent.
        """
        try:
            response = requests.get(
                'https://gmail.googleapis.com/gmail/v1/users/me/profile',
                headers={'Authorization': f'Bearer {access_token}'},
                timeout=10,
            )
            response.raise_for_status()
            return response.json().get('emailAddress')
        except requests.exceptions.RequestException as e:
            _logger.warning('[Gmail API] Could not fetch user email: %s', self._error_detail(e))
            return None

    # -------------------------------------------------------------------------
    # Error helpers
    # -------------------------------------------------------------------------
    def _error_json(self, exc):
        if hasattr(exc, 'response') and exc.response is not None:
            try:
                return exc.response.json()
            except ValueError:
                return {}
        return {}

    def _error_code(self, exc):
        """Google returns errors two ways: {'error': 'invalid_grant', ...} on the
        token endpoint, {'error': {'status': ...}} on the API. Handle both."""
        err = self._error_json(exc).get('error')
        if isinstance(err, dict):
            return err.get('status')
        return err

    def _error_detail(self, exc):
        payload = self._error_json(exc)
        err = payload.get('error')
        if isinstance(err, dict):
            return err.get('message', str(exc))
        if err:
            return payload.get('error_description', err)
        return str(exc)
