# -*- coding: utf-8 -*-
import base64
import logging
import mimetypes
import requests
import secrets
import time
from datetime import datetime, timedelta
from odoo import models, api, _
from odoo.exceptions import UserError
from . import encryption_utils

_logger = logging.getLogger(__name__)

# Rate limiting configuration
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 2


class MicrosoftGraphClient(models.AbstractModel):
    """Helper model for Microsoft Graph API calls"""
    _name = 'microsoft.graph.client'
    _description = 'Microsoft Graph API Client'

    @api.model
    def _get_config_params(self):
        """Get Microsoft OAuth configuration from settings"""
        ICP = self.env['ir.config_parameter'].sudo()

        # Get encrypted client secret and decrypt it
        encrypted_secret = ICP.get_param('x_pan_outlook_pro.client_secret_encrypted')
        client_secret = encryption_utils.decrypt_value(
            self.env,
            encrypted_secret
        ) if encrypted_secret else False

        return {
            'client_id': ICP.get_param('x_pan_outlook_pro.client_id'),
            'client_secret': client_secret,
            'tenant_id': ICP.get_param('x_pan_outlook_pro.tenant_id'),
            'token_url': ICP.get_param('x_pan_outlook_pro.token_url',
                                       'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token'),
        }

    @api.model
    def generate_oauth_state(self):
        """Generate a cryptographically secure state token for CSRF protection."""
        return secrets.token_urlsafe(32)

    @api.model
    def get_authorization_url(self, redirect_uri, state=None):
        """Generate OAuth authorization URL with CSRF state parameter.

        Args:
            redirect_uri: The OAuth callback URL
            state: CSRF state token (generated via generate_oauth_state())

        Returns:
            str: The authorization URL to redirect the user to
        """
        config = self._get_config_params()
        tenant_id = config['tenant_id']
        client_id = config['client_id']

        if not tenant_id or not client_id:
            raise UserError(_('Please configure Microsoft Client ID and Tenant ID in Settings.'))

        auth_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize"

        # Required scopes for sending and reading mail from shared mailboxes
        # Mail.ReadWrite is needed for Draft→Send flow (creating drafts requires write access)
        scopes = [
            'openid',
            'profile',
            'email',
            'offline_access',
            'User.Read',
            'Mail.ReadWrite',
            'Mail.ReadWrite.Shared',
        ]

        params = {
            'client_id': client_id,
            'response_type': 'code',
            'redirect_uri': redirect_uri,
            'scope': ' '.join(scopes),
            'response_mode': 'query',
        }

        # Add state parameter for CSRF protection
        if state:
            params['state'] = state

        query_string = '&'.join([f'{k}={requests.utils.quote(str(v))}' for k, v in params.items()])
        return f"{auth_url}?{query_string}"

    @api.model
    def exchange_code_for_tokens(self, authorization_code, redirect_uri):
        """Exchange authorization code for access and refresh tokens"""
        config = self._get_config_params()
        token_url = config['token_url'].format(tenant=config['tenant_id'])

        data = {
            'client_id': config['client_id'],
            'client_secret': config['client_secret'],
            'code': authorization_code,
            'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code',
        }

        try:
            response = requests.post(token_url, data=data, timeout=10)
            response.raise_for_status()
            token_data = response.json()

            # Calculate token expiry time
            expires_in = token_data.get('expires_in', 3600)
            expiry = datetime.now() + timedelta(seconds=expires_in)

            return {
                'access_token': token_data.get('access_token'),
                'refresh_token': token_data.get('refresh_token'),
                'token_expiry': expiry,
            }
        except requests.exceptions.RequestException as e:
            # Log detailed error information
            error_detail = str(e)
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_json = e.response.json()
                    error_detail = f"{e}\nMicrosoft error: {error_json.get('error', 'unknown')}\nDescription: {error_json.get('error_description', 'no description')}"
                    _logger.error(f"Token exchange failed. Request data: client_id={config['client_id']}, redirect_uri={redirect_uri}, tenant_id={config['tenant_id']}")
                    _logger.error(f"Microsoft response: {error_json}")
                except (ValueError, KeyError):
                    pass
            _logger.error(f"Failed to exchange code for tokens: {error_detail}")
            raise UserError(_('Failed to authenticate with Microsoft: %s') % error_detail)

    @api.model
    def refresh_access_token(self, user):
        """Refresh access token using refresh token"""
        if not user.x_microsoft_refresh_token:
            raise UserError(_('No refresh token available. Please reconnect your Microsoft account.'))

        config = self._get_config_params()
        token_url = config['token_url'].format(tenant=config['tenant_id'])

        data = {
            'client_id': config['client_id'],
            'client_secret': config['client_secret'],
            'refresh_token': user.x_microsoft_refresh_token,
            'grant_type': 'refresh_token',
        }

        try:
            response = requests.post(token_url, data=data, timeout=10)
            response.raise_for_status()
            token_data = response.json()

            expires_in = token_data.get('expires_in', 3600)
            expiry = datetime.now() + timedelta(seconds=expires_in)

            # Use sudo() because token fields have groups='base.group_system'
            user.sudo().write({
                'x_microsoft_access_token': token_data.get('access_token'),
                'x_microsoft_refresh_token': token_data.get('refresh_token', user.x_microsoft_refresh_token),
                'x_microsoft_token_expiry': expiry,
            })

            return token_data.get('access_token')
        except requests.exceptions.RequestException as e:
            error_code = None
            error_description = str(e)

            # Extract error details from Microsoft response
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_json = e.response.json()
                    error_code = error_json.get('error')
                    error_description = error_json.get('error_description', str(e))
                except (ValueError, KeyError):
                    pass

            _logger.error(f"Failed to refresh token for user {user.id}: {error_code} - {error_description}")

            # Check for permanent failures that require re-authentication
            # invalid_grant: token revoked, expired, or user changed password
            # invalid_client: app credentials changed
            permanent_errors = ('invalid_grant', 'invalid_client', 'unauthorized_client')
            if error_code in permanent_errors:
                _logger.warning(f"[OAuth] Permanent token failure for user {user.id}, clearing tokens")
                # Clear invalid tokens so user can reconnect
                user.sudo().write({
                    'x_microsoft_access_token_encrypted': False,
                    'x_microsoft_refresh_token_encrypted': False,
                    'x_microsoft_token_expiry': False,
                })
                raise UserError(_(
                    'Your Microsoft connection has expired or been revoked. '
                    'Please reconnect your Microsoft account.'
                ))

            raise UserError(_('Failed to refresh Microsoft token: %s') % error_description)

    @api.model
    def get_valid_token(self, user):
        """Get a valid access token, refreshing if necessary"""
        # Check if token is expired or about to expire (5 min buffer)
        if user.x_microsoft_token_expiry:
            buffer_time = datetime.now() + timedelta(minutes=5)
            if user.x_microsoft_token_expiry <= buffer_time:
                _logger.info(f"Token expired for user {user.id}, refreshing...")
                return self.refresh_access_token(user)

        if not user.x_microsoft_access_token:
            raise UserError(_('No access token available. Please connect your Microsoft account.'))

        return user.x_microsoft_access_token

    @api.model
    def test_connection(self, user):
        """Test Graph API connection by fetching user info"""
        token = self.get_valid_token(user)

        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        }

        try:
            response = requests.get('https://graph.microsoft.com/v1.0/me', headers=headers, timeout=10)
            response.raise_for_status()
            user_info = response.json()

            return {
                'success': True,
                'display_name': user_info.get('displayName'),
                'email': user_info.get('mail') or user_info.get('userPrincipalName'),
                'id': user_info.get('id'),
            }
        except requests.exceptions.RequestException as e:
            _logger.error(f"Graph API connection test failed: {e}")
            return {
                'success': False,
                'error': str(e),
            }

    @api.model
    def send_email_via_graph(self, mail_record, mailbox, user):
        """
        Send email via Microsoft Graph API using Draft → Send flow.

        Creates a draft first (which returns internetMessageId and conversationId),
        then sends it. This enables proper duplicate detection and threading.

        Args:
            mail_record: mail.mail record to send
            mailbox: x_microsoft.mailbox record to send from
            user: res.users record with valid Microsoft OAuth token

        Returns:
            dict: {
                'success': bool,
                'error': str (if failed),
                'microsoft_message_id': str (internetMessageId from Microsoft),
                'microsoft_conversation_id': str (conversationId from Microsoft)
            }
        """
        try:
            # Use delegated token from the user (principle of least privilege)
            token = self.get_valid_token(user)

            # Get the correct identifier for Graph API (UPN or email)
            graph_user_id = mailbox.get_graph_user_id()
            mailbox_email = mailbox.email

            _logger.info(f"[Graph API] Using delegated token for user {user.login} to send from mailbox: {mailbox_email}")

            # Parse To recipients from both email_to and recipient_ids (partners)
            to_recipients = []

            # Add recipients from email_to field (comma-separated emails)
            if mail_record.email_to:
                for email in mail_record.email_to.split(','):
                    email = email.strip()
                    if email:
                        to_recipients.append({
                            'emailAddress': {
                                'address': email
                            }
                        })

            # Add recipients from recipient_ids (Odoo partners)
            if mail_record.recipient_ids:
                for partner in mail_record.recipient_ids:
                    if partner.email:
                        to_recipients.append({
                            'emailAddress': {
                                'address': partner.email,
                                'name': partner.name
                            }
                        })

            # Check if we have any recipients at all
            if not to_recipients:
                return {
                    'success': False,
                    'error': 'No recipients specified (no email_to or recipient_ids with emails)'
                }

            # Build custom headers for tracking
            internet_message_headers = []

            # Add model and record ID if available
            if mail_record.model and mail_record.res_id:
                internet_message_headers.extend([
                    {'name': 'X-Odoo-Model', 'value': mail_record.model},
                    {'name': 'X-Odoo-Record-Id', 'value': str(mail_record.res_id)},
                ])

            # Add mail.mail ID
            internet_message_headers.append({
                'name': 'X-Odoo-Mail-Id',
                'value': str(mail_record.id)
            })

            # Add mail.message ID if available (for replies)
            if mail_record.mail_message_id:
                internet_message_headers.append({
                    'name': 'X-Odoo-Message-Id',
                    'value': str(mail_record.mail_message_id.id)
                })

            # Build attachments list from mail.mail attachment_ids
            attachments = []
            if mail_record.attachment_ids:
                for attachment in mail_record.attachment_ids:
                    # Get MIME type
                    content_type = attachment.mimetype or mimetypes.guess_type(attachment.name)[0] or 'application/octet-stream'

                    # Attachment data is already base64 encoded in Odoo
                    attachment_data = attachment.datas
                    if attachment_data:
                        attachments.append({
                            '@odata.type': '#microsoft.graph.fileAttachment',
                            'name': attachment.name,
                            'contentType': content_type,
                            'contentBytes': attachment_data.decode('utf-8') if isinstance(attachment_data, bytes) else attachment_data
                        })

            # Build message payload for draft creation
            message = {
                'subject': mail_record.subject or '(No Subject)',
                'body': {
                    'contentType': 'HTML',
                    'content': mail_record.body_html or mail_record.body or ''
                },
                'toRecipients': to_recipients,
                'from': {
                    'emailAddress': {
                        'address': mailbox_email
                    }
                },
                'internetMessageHeaders': internet_message_headers
            }

            # Add attachments if present
            if attachments:
                message['attachments'] = attachments

            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
            }

            # Build recipient list for logging
            recipient_emails = [r['emailAddress'].get('address', 'NO_ADDRESS') for r in to_recipients]
            _logger.info(f"[Graph API] Sending email from {mailbox_email} to {recipient_emails}")

            # Step 1: Create draft message - this gives us internetMessageId and conversationId
            draft_url = f'https://graph.microsoft.com/v1.0/users/{graph_user_id}/messages'
            draft_response = requests.post(draft_url, headers=headers, json=message, timeout=30)
            draft_response.raise_for_status()

            draft_data = draft_response.json()
            draft_id = draft_data.get('id')
            microsoft_message_id = draft_data.get('internetMessageId')
            microsoft_conversation_id = draft_data.get('conversationId')

            _logger.info(f"[Graph API] Created draft - Message-ID: {microsoft_message_id}, Conversation-ID: {microsoft_conversation_id}")

            # Step 2: Send the draft
            send_url = f'https://graph.microsoft.com/v1.0/users/{graph_user_id}/messages/{draft_id}/send'
            send_response = requests.post(send_url, headers=headers, timeout=30)
            send_response.raise_for_status()

            _logger.info(f"[Graph API] Successfully sent email: {mail_record.subject}")

            return {
                'success': True,
                'microsoft_message_id': microsoft_message_id,
                'microsoft_conversation_id': microsoft_conversation_id,
            }

        except requests.exceptions.RequestException as e:
            error_detail = str(e)
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_json = e.response.json()
                    error_detail = f"{e}\nGraph API error: {error_json.get('error', {}).get('message', 'unknown')}"
                    _logger.error(f"Graph API response: {error_json}")
                except (ValueError, KeyError):
                    pass

            _logger.error(f"Failed to send email via Graph API: {error_detail}")
            return {
                'success': False,
                'error': error_detail
            }
        except Exception as e:
            _logger.exception("Unexpected error sending email via Graph API")
            return {
                'success': False,
                'error': str(e)
            }

    # -------------------------------------------------------------------------
    # Incoming Mail Methods
    # -------------------------------------------------------------------------

    @api.model
    def fetch_messages(self, user, mailbox_email, folder='Inbox', since_datetime=None, top=50):
        """
        Fetch messages from a Microsoft mailbox folder via Graph API.

        Args:
            user: res.users record with OAuth tokens
            mailbox_email: Email address of the mailbox to fetch from
            folder: Folder name ('Inbox', 'SentItems', etc.)
            since_datetime: Only fetch messages received after this datetime
            top: Maximum number of messages to fetch

        Returns:
            list[dict]: List of message objects from Graph API
        """
        token = self.get_valid_token(user)

        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        }

        # Build URL - use /users/{email} for shared mailboxes
        url = f'https://graph.microsoft.com/v1.0/users/{mailbox_email}/mailFolders/{folder}/messages'

        # Build query parameters
        params = {
            '$top': top,
            '$orderby': 'receivedDateTime asc',
            '$select': 'id,internetMessageId,subject,from,toRecipients,ccRecipients,'
                       'receivedDateTime,bodyPreview,hasAttachments,isRead',
        }

        # Add filter for messages after since_datetime
        if since_datetime:
            # Format datetime for OData filter
            filter_time = since_datetime.strftime('%Y-%m-%dT%H:%M:%SZ')
            params['$filter'] = f"receivedDateTime gt {filter_time}"

        try:
            response = self._request_with_retry('get', url, headers=headers, params=params, timeout=30)
            response.raise_for_status()

            data = response.json()
            messages = data.get('value', [])
            _logger.info(f"[Graph API] Fetched {len(messages)} messages from {mailbox_email}/{folder}")

            return messages

        except requests.exceptions.RequestException as e:
            error_detail = self._extract_graph_error(e)
            _logger.error(f"[Graph API] Failed to fetch messages: {error_detail}")
            raise UserError(_('Failed to fetch messages from Microsoft: %s') % error_detail)

    @api.model
    def get_message_with_headers(self, user, mailbox_email, message_id):
        """
        Get full message details including internet headers for threading.

        Args:
            user: res.users record with OAuth tokens
            mailbox_email: Email address of the mailbox
            message_id: Graph API message ID

        Returns:
            dict: Full message object with headers
        """
        token = self.get_valid_token(user)

        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        }

        url = f'https://graph.microsoft.com/v1.0/users/{mailbox_email}/messages/{message_id}'

        params = {
            '$select': 'id,internetMessageId,internetMessageHeaders,conversationId,subject,from,'
                       'toRecipients,ccRecipients,receivedDateTime,body,hasAttachments,isRead',
        }

        try:
            response = self._request_with_retry('get', url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as e:
            error_detail = self._extract_graph_error(e)
            _logger.error(f"[Graph API] Failed to get message details: {error_detail}")
            raise UserError(_('Failed to get message details: %s') % error_detail)

    @api.model
    def get_message_attachments(self, user, mailbox_email, message_id):
        """
        Get attachments for a message.

        Args:
            user: res.users record with OAuth tokens
            mailbox_email: Email address of the mailbox
            message_id: Graph API message ID

        Returns:
            list[dict]: List of attachment objects
        """
        token = self.get_valid_token(user)

        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        }

        url = f'https://graph.microsoft.com/v1.0/users/{mailbox_email}/messages/{message_id}/attachments'

        try:
            response = self._request_with_retry('get', url, headers=headers, timeout=30)
            response.raise_for_status()

            data = response.json()
            return data.get('value', [])

        except requests.exceptions.RequestException as e:
            error_detail = self._extract_graph_error(e)
            _logger.error(f"[Graph API] Failed to get attachments: {error_detail}")
            return []  # Don't fail the whole process for attachment errors

    @api.model
    def _extract_graph_error(self, exception):
        """Extract detailed error message from Graph API response."""
        error_detail = str(exception)
        if hasattr(exception, 'response') and exception.response is not None:
            try:
                error_json = exception.response.json()
                error_obj = error_json.get('error', {})
                # Handle both dict (Graph API) and string (OAuth) error formats
                if isinstance(error_obj, dict):
                    error_msg = error_obj.get('message', 'unknown')
                    error_code = error_obj.get('code', 'unknown')
                    error_detail = f"{error_code}: {error_msg}"
                else:
                    # OAuth errors return error as string
                    error_desc = error_json.get('error_description', str(error_obj))
                    error_detail = f"{error_obj}: {error_desc}"
            except (ValueError, KeyError, AttributeError):
                pass
        return error_detail

    def _request_with_retry(self, method, url, headers, timeout=30, **kwargs):
        """
        Execute HTTP request with rate limiting and exponential backoff.

        Handles Microsoft Graph API rate limiting (HTTP 429) by:
        - Reading Retry-After header when present
        - Using exponential backoff for transient errors
        - Retrying up to MAX_RETRIES times

        Args:
            method: HTTP method ('get', 'post', etc.)
            url: Request URL
            headers: Request headers
            timeout: Request timeout in seconds
            **kwargs: Additional arguments for requests (json, data, params, etc.)

        Returns:
            requests.Response: The successful response

        Raises:
            requests.exceptions.RequestException: If all retries fail
        """
        last_exception = None
        backoff = INITIAL_BACKOFF_SECONDS

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = getattr(requests, method)(url, headers=headers, timeout=timeout, **kwargs)

                # Check for rate limiting
                if response.status_code == 429:
                    retry_after = response.headers.get('Retry-After')
                    if retry_after:
                        wait_time = int(retry_after)
                    else:
                        wait_time = backoff

                    if attempt < MAX_RETRIES:
                        _logger.warning(f"[Graph API] Rate limited (429), waiting {wait_time}s before retry {attempt + 1}/{MAX_RETRIES}")
                        time.sleep(wait_time)
                        backoff *= 2  # Exponential backoff
                        continue
                    else:
                        response.raise_for_status()  # Raise on final attempt

                # Check for other server errors that might be transient
                if response.status_code in (500, 502, 503, 504) and attempt < MAX_RETRIES:
                    _logger.warning(f"[Graph API] Server error ({response.status_code}), retrying in {backoff}s ({attempt + 1}/{MAX_RETRIES})")
                    time.sleep(backoff)
                    backoff *= 2
                    continue

                return response

            except requests.exceptions.Timeout as e:
                last_exception = e
                if attempt < MAX_RETRIES:
                    _logger.warning(f"[Graph API] Request timeout, retrying in {backoff}s ({attempt + 1}/{MAX_RETRIES})")
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                raise

            except requests.exceptions.ConnectionError as e:
                last_exception = e
                if attempt < MAX_RETRIES:
                    _logger.warning(f"[Graph API] Connection error, retrying in {backoff}s ({attempt + 1}/{MAX_RETRIES})")
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                raise

        # Should not reach here, but just in case
        if last_exception:
            raise last_exception
        raise requests.exceptions.RequestException("Max retries exceeded")

    @api.model
    def _get_token_identity(self, token):
        """
        Get the Microsoft identity associated with an OAuth token.

        This calls /me to determine which Microsoft account is actually
        associated with the token. Useful for debugging when emails are
        being sent from unexpected accounts.

        Args:
            token: Valid OAuth access token

        Returns:
            str: Description of the identity (email and display name)
        """
        try:
            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
            }

            response = requests.get('https://graph.microsoft.com/v1.0/me', headers=headers, timeout=10)
            response.raise_for_status()
            user_info = response.json()

            email = user_info.get('mail') or user_info.get('userPrincipalName') or 'NO_EMAIL'
            display_name = user_info.get('displayName') or 'NO_NAME'

            return f"{display_name} <{email}>"

        except Exception as e:
            _logger.warning(f"[Graph API] Could not fetch token identity: {e}")
            return "UNKNOWN (failed to fetch /me)"

    def get_user_email(self, token):
        """
        Get the email address of the authenticated Microsoft user.

        Args:
            token: Valid OAuth access token

        Returns:
            str: Email address or None if not available
        """
        try:
            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
            }

            response = requests.get('https://graph.microsoft.com/v1.0/me', headers=headers, timeout=10)
            response.raise_for_status()
            user_info = response.json()

            return user_info.get('mail') or user_info.get('userPrincipalName')

        except Exception as e:
            _logger.warning(f"[Graph API] Could not fetch user email: {e}")
            return None
