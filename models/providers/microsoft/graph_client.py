# -*- coding: utf-8 -*-
import base64
import logging
import mimetypes
import re
import requests
import secrets
import time
from datetime import datetime, timedelta
from odoo import models, api, _
from odoo.exceptions import UserError
from ... import encryption_utils
from ...mail_provider_client import FOLDER_INBOX, FOLDER_SENT

_logger = logging.getLogger(__name__)

# Rate limiting configuration
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 2

# Attachment size threshold: Graph API allows max 3MB per direct attachment upload.
# Larger files must use the upload session API (supports up to 150MB).
DIRECT_ATTACHMENT_LIMIT = 3 * 1024 * 1024  # 3MB in bytes


class MicrosoftGraphClient(models.AbstractModel):
    """Microsoft 365 implementation of the `mail.provider.client` contract.

    Everything Graph-specific lives here: URLs, OAuth endpoints, the
    draft-then-send flow, the 3MB attachment threshold, and the shapes of
    Graph's JSON payloads. Callers see only the normalized structures
    documented in `mail_provider_client.py`.
    """
    _name = 'microsoft.graph.client'
    _inherit = 'mail.provider.client'
    _description = 'Microsoft Graph API Client'

    # Microsoft 365 supports send-as on shared mailboxes: a user sends with
    # their own delegated token, given SendAs rights in Exchange.
    supports_shared_mailbox = True
    supports_delegation = False
    supported_mailbox_types = ('personal', 'shared', 'notification')

    # Odoo's folder vocabulary -> Graph's well-known folder names.
    _FOLDER_MAP = {
        FOLDER_INBOX: 'Inbox',
        FOLDER_SENT: 'SentItems',
    }

    @api.model
    def provider_code(self):
        return 'outlook'

    @api.model
    def provider_label(self):
        return 'Microsoft 365'

    @api.model
    def account_for_user(self, user):
        """Every Microsoft account hangs off a person, so this is a lookup."""
        return self.env['pan.mail.account']._for_user(user, self.provider_code())

    @api.model
    def resolve_sending_account(self, mailbox, author_user=None):
        """Notification and personal mailboxes send with the owner's token;
        shared mailboxes send with the author's own token (SendAs)."""
        return self.account_for_user(self._resolve_sending_user(mailbox, author_user))

    @api.model
    def resolve_receiving_account(self, mailbox):
        """Reading a Microsoft mailbox always uses the owner's delegated token."""
        return self.account_for_user(mailbox.x_owner_user_id)

    @api.model
    def _resolve_sending_user(self, mailbox, author_user=None):
        """Whose token sends from `mailbox`.

        Microsoft-specific by construction: every account here belongs to a
        person. Gmail answers the same question with a mailbox's own service
        account and no user at all, which is why `resolve_sending_account` is
        the contract method and this one is not.
        """
        if mailbox.x_mailbox_type in ('notification', 'personal'):
            return mailbox.x_owner_user_id
        if author_user:
            return author_user
        # In cron context env.user is the cron runner, not the sender — which is
        # why the author is preferred above and this is only the last resort.
        env_user = self.env.user
        if env_user and not env_user._is_public():
            return env_user
        return self.env['res.users']

    @api.model
    def _graph_folder(self, folder):
        """Translate a contract folder id into a Graph folder name."""
        try:
            return self._FOLDER_MAP[folder]
        except KeyError:
            raise UserError(_('Unknown mail folder: %s') % folder)

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
    def _exchange_code_for_tokens(self, authorization_code, redirect_uri):
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
    def refresh_access_token(self, account):
        """Refresh access token using refresh token"""
        if not account.refresh_token:
            raise UserError(_('No refresh token available. Please reconnect your Microsoft account.'))

        config = self._get_config_params()
        token_url = config['token_url'].format(tenant=config['tenant_id'])

        data = {
            'client_id': config['client_id'],
            'client_secret': config['client_secret'],
            'refresh_token': account.refresh_token,
            'grant_type': 'refresh_token',
        }

        try:
            response = requests.post(token_url, data=data, timeout=10)
            response.raise_for_status()
            token_data = response.json()

            expires_in = token_data.get('expires_in', 3600)
            expiry = datetime.now() + timedelta(seconds=expires_in)

            # sudo(): the token fields have groups='base.group_system'
            account.sudo().write({
                'access_token': token_data.get('access_token'),
                'refresh_token': token_data.get('refresh_token', account.refresh_token),
                'token_expiry': expiry,
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

            _logger.error(f"Failed to refresh token for {account.email}: {error_code} - {error_description}")

            # Check for permanent failures that require re-authentication
            # invalid_grant: token revoked, expired, or user changed password
            # invalid_client: app credentials changed
            permanent_errors = ('invalid_grant', 'invalid_client', 'unauthorized_client')
            if error_code in permanent_errors:
                _logger.warning(f"[OAuth] Permanent token failure for {account.email}, clearing tokens")
                # Clear invalid tokens so user can reconnect
                account.sudo().write({
                    'access_token_encrypted': False,
                    'refresh_token_encrypted': False,
                    'token_expiry': False,
                })
                raise UserError(_(
                    'Your Microsoft connection has expired or been revoked. '
                    'Please reconnect your Microsoft account.'
                ))

            raise UserError(_('Failed to refresh Microsoft token: %s') % error_description)

    @api.model
    def get_valid_token(self, account):
        """Get a valid access token for `account`, refreshing if necessary."""
        # Check if token is expired or about to expire (5 min buffer)
        if account.token_expiry:
            buffer_time = datetime.now() + timedelta(minutes=5)
            if account.token_expiry <= buffer_time:
                _logger.info(f"Token expired for {account.email}, refreshing...")
                return self.refresh_access_token(account)

        if not account.access_token:
            raise UserError(_('No access token available. Please connect your Microsoft account.'))

        return account.access_token

    @api.model
    def test_connection(self, account):
        """Test Graph API connection by fetching user info"""
        token = self.get_valid_token(account)

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
    def _prepare_inline_images(self, body_html):
        """
        Convert /web/image/ references in HTML body to cid: inline attachments.

        Parses the body for <img src="/web/image/ID..."> references, loads the
        corresponding ir.attachment records, and replaces the URLs with cid:
        references. Returns the modified body and Graph API inline attachment dicts.

        Args:
            body_html: HTML body string

        Returns:
            tuple: (processed_body, inline_attachments, inline_attachment_ids)
                - processed_body: HTML with cid: references
                - inline_attachments: list of Graph API attachment dicts (isInline=True)
                - inline_attachment_ids: set of ir.attachment IDs used inline
        """
        if not body_html:
            return body_html, [], set()

        inline_attachments = []
        inline_att_ids = set()
        counter = [0]

        def _replace_with_cid(match):
            try:
                att_id = int(match.group(1))
            except (ValueError, TypeError):
                return match.group(0)

            attachment = self.env['ir.attachment'].sudo().browse(att_id)
            if not attachment.exists() or not attachment.datas:
                return match.group(0)

            counter[0] += 1
            content_id = f"odoo_inline_image_{counter[0]}"

            content_type = attachment.mimetype or mimetypes.guess_type(attachment.name or '')[0] or 'application/octet-stream'
            att_data = attachment.datas

            inline_attachments.append({
                '@odata.type': '#microsoft.graph.fileAttachment',
                'name': attachment.name or f'image{counter[0]}.png',
                'contentType': content_type,
                'contentBytes': att_data.decode('utf-8') if isinstance(att_data, bytes) else att_data,
                'isInline': True,
                'contentId': content_id,
            })
            inline_att_ids.add(att_id)

            return f'src="cid:{content_id}"'

        processed_body = re.sub(r'src="[^"]*?/web/image/(\d+)[^"]*"', _replace_with_cid, body_html)

        if inline_attachments:
            _logger.info(f"[Graph API] Converted {len(inline_attachments)} inline image(s) to cid: references")

        return processed_body, inline_attachments, inline_att_ids

    @api.model
    def _add_attachment_to_draft(self, headers, graph_user_id, draft_id, attachment_dict):
        """
        Add a small attachment (< 3MB) directly to a draft message.

        Uses POST /messages/{id}/attachments with the attachment JSON payload.
        This is the standard approach for attachments under 3MB.
        """
        url = f'https://graph.microsoft.com/v1.0/users/{graph_user_id}/messages/{draft_id}/attachments'
        response = requests.post(url, headers=headers, json=attachment_dict, timeout=30)
        response.raise_for_status()
        _logger.info(f"[Graph API] Added attachment '{attachment_dict['name']}' to draft")

    @api.model
    def _upload_large_attachment(self, headers, graph_user_id, draft_id, name, content_type, raw_bytes, is_inline=False):
        """
        Upload a large attachment (>= 3MB) via upload session.

        Uses the Graph API upload session flow:
        1. Create upload session with attachment metadata
        2. Upload file in chunks (each chunk < 4MB, must be multiple of 320KB)
        3. Session completes automatically after last chunk

        Args:
            headers: Auth headers (used for session creation only, not for chunk uploads)
            graph_user_id: Microsoft user ID / UPN
            draft_id: Draft message ID
            name: Attachment filename
            content_type: MIME type
            raw_bytes: Raw file content (not base64)
            is_inline: Whether this is an inline image
        """
        total_size = len(raw_bytes)
        _logger.info(f"[Graph API] Uploading large attachment '{name}' ({total_size} bytes) via upload session")

        # Step 1: Create upload session
        session_url = f'https://graph.microsoft.com/v1.0/users/{graph_user_id}/messages/{draft_id}/attachments/createUploadSession'
        session_payload = {
            'AttachmentItem': {
                '@odata.type': 'microsoft.graph.attachmentItem',
                'attachmentType': 'file',
                'name': name,
                'size': total_size,
                'contentType': content_type,
                'isInline': is_inline,
            }
        }
        session_response = requests.post(session_url, headers=headers, json=session_payload, timeout=30)
        session_response.raise_for_status()
        upload_url = session_response.json()['uploadUrl']

        # Step 2: Upload in chunks (max 4MB, must be multiple of 320KB)
        # Use ~4MB chunks (4 * 1024 * 1024 = 4194304, nearest 320KB multiple = 4177920)
        chunk_size = 320 * 1024 * 13  # ~4MB, multiple of 320KB
        offset = 0

        while offset < total_size:
            chunk_end = min(offset + chunk_size, total_size) - 1
            chunk_data = raw_bytes[offset:chunk_end + 1]

            # Upload URL has embedded auth — do NOT include Authorization header
            chunk_headers = {
                'Content-Type': 'application/octet-stream',
                'Content-Length': str(len(chunk_data)),
                'Content-Range': f'bytes {offset}-{chunk_end}/{total_size}',
            }

            chunk_response = requests.put(upload_url, headers=chunk_headers, data=chunk_data, timeout=60)
            chunk_response.raise_for_status()
            offset = chunk_end + 1

        _logger.info(f"[Graph API] Upload complete for '{name}'")

    def _create_draft(self, headers, graph_user_id, message, reply_to_provider_id=None):
        """Create the draft to send, threaded onto a parent message if we can.

        Graph will not let us set `In-Reply-To` or `References`:
        `internetMessageHeaders` accepts custom `x-` headers only. So the only
        way to emit a properly threaded reply is to ask Graph to build one with
        `createReply` — the draft it hands back already carries the right
        In-Reply-To, References and conversationId — and then PATCH our own
        subject, body and recipients over the quoted stub it prefilled.

        Falls back to a plain draft when there is no parent, or when the parent
        has gone: message ids go stale the moment a user moves or deletes the
        mail in Outlook, and a mail that threads badly still beats a mail that
        never goes out.

        Returns the draft's JSON, including `id`, `internetMessageId` and
        `conversationId`.
        """
        base_url = f'https://graph.microsoft.com/v1.0/users/{graph_user_id}/messages'

        if reply_to_provider_id:
            try:
                reply_response = requests.post(
                    f'{base_url}/{reply_to_provider_id}/createReply',
                    headers=headers, timeout=30,
                )
                reply_response.raise_for_status()
                draft_id = reply_response.json().get('id')
                if draft_id:
                    patch_response = requests.patch(
                        f'{base_url}/{draft_id}',
                        headers=headers, json=message, timeout=30,
                    )
                    patch_response.raise_for_status()
                    _logger.info(
                        f"[Graph API] Threaded reply onto message {reply_to_provider_id}"
                    )
                    return patch_response.json()
                _logger.warning(
                    f"[Graph API] createReply on {reply_to_provider_id} returned no draft id; "
                    f"sending unthreaded"
                )
            except requests.exceptions.RequestException as e:
                _logger.warning(
                    f"[Graph API] createReply on {reply_to_provider_id} failed ({e}); "
                    f"sending unthreaded"
                )

        response = requests.post(base_url, headers=headers, json=message, timeout=30)
        response.raise_for_status()
        return response.json()

    @api.model
    def send_email_via_graph(self, mail_record, mailbox, account, reply_context=None):
        """
        Send email via Microsoft Graph API using Draft → Send flow.

        Creates a draft first (which returns internetMessageId and conversationId),
        then sends it. This enables proper duplicate detection and threading.

        Args:
            mail_record: mail.mail record to send
            mailbox: x_microsoft.mailbox record to send from
            account: pan.mail.account holding a valid Microsoft OAuth token
            reply_context: optional threading hints (see the contract). Only
                `provider_message_id` is usable here — Graph will not accept
                In-Reply-To or References — and it selects the createReply
                draft flow instead of a plain one.

        Returns:
            dict: {
                'success': bool,
                'error': str (if failed),
                'microsoft_message_id': str (internetMessageId from Microsoft),
                'microsoft_conversation_id': str (conversationId from Microsoft)
            }
        """
        try:
            # Use the account's delegated token (principle of least privilege)
            token = self.get_valid_token(account)

            # Get the correct identifier for Graph API (UPN or email)
            # Graph addresses a mailbox by its email in /users/{id}/...
            graph_user_id = mailbox.email
            mailbox_email = mailbox.email

            _logger.info(f"[Graph API] Using delegated token for {account.email} to send from mailbox: {mailbox_email}")

            # Parse To recipients from both email_to and recipient_ids (partners)
            from email.utils import parseaddr

            def _parse_address_list(raw_value):
                """Parse a comma-separated RFC 5322 address list into Graph recipient dicts."""
                result = []
                if not raw_value:
                    return result
                for raw in raw_value.split(','):
                    raw = raw.strip()
                    if not raw:
                        continue
                    name, address = parseaddr(raw)
                    if address:
                        recipient = {'emailAddress': {'address': address}}
                        if name:
                            recipient['emailAddress']['name'] = name
                        result.append(recipient)
                return result

            to_recipients = _parse_address_list(mail_record.email_to)

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

            # Parse CC recipients from email_cc (set by Odoo core when Sign/composer adds CC)
            cc_recipients = _parse_address_list(mail_record.email_cc)

            # Check if we have any recipients at all
            if not to_recipients and not cc_recipients:
                return {
                    'success': False,
                    'error': 'No recipients specified (no email_to, recipient_ids, or email_cc with emails)',
                    # Distinguishable code so mail.mail.send() can skip+cancel this
                    # mail (typically an internal notification to a user/partner
                    # without an email address) instead of aborting the whole batch.
                    'error_code': 'no_recipients',
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

            # Process body: convert /web/image/ URLs to cid: inline attachments
            # This embeds images directly in the email so they work regardless
            # of whether the Odoo server is publicly accessible
            body_html = mail_record.body_html or mail_record.body or ''
            body_html, inline_attachments, inline_att_ids = self._prepare_inline_images(body_html)

            # Build regular attachments (skip those already embedded inline)
            regular_attachments = []
            if mail_record.attachment_ids:
                for attachment in mail_record.attachment_ids:
                    if attachment.id in inline_att_ids:
                        continue
                    content_type = attachment.mimetype or mimetypes.guess_type(attachment.name)[0] or 'application/octet-stream'
                    attachment_data = attachment.datas
                    if attachment_data:
                        regular_attachments.append({
                            '@odata.type': '#microsoft.graph.fileAttachment',
                            'name': attachment.name,
                            'contentType': content_type,
                            'contentBytes': attachment_data.decode('utf-8') if isinstance(attachment_data, bytes) else attachment_data,
                        })

            all_attachments = inline_attachments + regular_attachments

            # Build message payload for draft creation (WITHOUT attachments —
            # attachments are added separately to avoid the 4MB JSON payload limit)
            message = {
                'subject': mail_record.subject or '(No Subject)',
                'body': {
                    'contentType': 'HTML',
                    'content': body_html
                },
                'toRecipients': to_recipients,
                'from': {
                    'emailAddress': {
                        'address': mailbox_email
                    }
                },
                'internetMessageHeaders': internet_message_headers
            }
            if cc_recipients:
                message['ccRecipients'] = cc_recipients

            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
            }

            # Build recipient list for logging
            recipient_emails = [r['emailAddress'].get('address', 'NO_ADDRESS') for r in to_recipients]
            cc_emails = [r['emailAddress'].get('address', 'NO_ADDRESS') for r in cc_recipients]
            _logger.info(f"[Graph API] Sending email from {mailbox_email} to {recipient_emails} cc {cc_emails}")

            # Step 1: Create draft (body + headers only), threaded when we know
            # which message this answers.
            draft_data = self._create_draft(
                headers, graph_user_id, message,
                reply_to_provider_id=(reply_context or {}).get('provider_message_id'),
            )
            draft_id = draft_data.get('id')
            microsoft_message_id = draft_data.get('internetMessageId')
            microsoft_conversation_id = draft_data.get('conversationId')

            _logger.info(f"[Graph API] Created draft - Message-ID: {microsoft_message_id}, Conversation-ID: {microsoft_conversation_id}")

            # Step 2: Add attachments to draft
            for att in all_attachments:
                raw_bytes = base64.b64decode(att['contentBytes'])
                if len(raw_bytes) < DIRECT_ATTACHMENT_LIMIT:
                    self._add_attachment_to_draft(headers, graph_user_id, draft_id, att)
                else:
                    self._upload_large_attachment(
                        headers, graph_user_id, draft_id,
                        name=att['name'],
                        content_type=att['contentType'],
                        raw_bytes=raw_bytes,
                        is_inline=att.get('isInline', False),
                    )

            # Step 3: Send the draft
            send_url = f'https://graph.microsoft.com/v1.0/users/{graph_user_id}/messages/{draft_id}/send'
            send_response = requests.post(send_url, headers=headers, timeout=30)
            send_response.raise_for_status()

            _logger.info("[Graph API] Successfully sent email %s", microsoft_message_id)

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

    @api.model
    def send_message(self, mail_record, mailbox, account, reply_context=None):
        """Send one mail.mail and return a normalized send result.

        Thin adapter over `send_email_via_graph`, which owns the Graph-specific
        draft-then-send flow, inline-image handling and attachment upload.
        """
        result = self.send_email_via_graph(
            mail_record=mail_record,
            mailbox=mailbox,
            account=account,
            reply_context=reply_context,
        )
        return {
            'success': result.get('success', False),
            'error': result.get('error'),
            'error_code': result.get('error_code'),
            'message_id': result.get('microsoft_message_id'),
            'thread_id': result.get('microsoft_conversation_id'),
        }

    # -------------------------------------------------------------------------
    # Incoming Mail — contract implementation
    #
    # The public methods below satisfy `mail.provider.client` and hand back
    # normalized dicts. The `_graph_*` helpers underneath them are the only
    # code that touches Graph's payload shapes.
    # -------------------------------------------------------------------------

    @api.model
    def fetch_messages(self, account, mailbox, folder=FOLDER_INBOX,
                       since_datetime=None, limit=50):
        """List messages in a folder, oldest first (see contract)."""
        raw_messages = self._graph_fetch_messages(
            account=account,
            mailbox_email=mailbox.email,
            folder=self._graph_folder(folder),
            since_datetime=since_datetime,
            top=limit,
        )
        return [self._normalize_message(msg) for msg in raw_messages]

    @api.model
    def get_message(self, account, mailbox, provider_message_id):
        """Fetch one message in full, including headers and body."""
        raw = self._graph_get_message(
            account=account,
            mailbox_email=mailbox.email,
            message_id=provider_message_id,
        )
        return self._normalize_message(raw)

    @api.model
    def get_message_attachments(self, account, mailbox, provider_message_id):
        """Return normalized attachments; never raises (see contract)."""
        raw_attachments = self._graph_get_attachments(
            account=account,
            mailbox_email=mailbox.email,
            message_id=provider_message_id,
        )

        attachments = []
        for raw in raw_attachments:
            # Graph also returns itemAttachment / referenceAttachment, which
            # carry no bytes we can store as an ir.attachment.
            if raw.get('@odata.type') != '#microsoft.graph.fileAttachment':
                continue
            content_b64 = raw.get('contentBytes')
            if not content_b64:
                continue
            name = raw.get('name') or 'unnamed'
            try:
                content = base64.b64decode(content_b64)
            except Exception as e:
                _logger.warning(f"[Graph API] Failed to decode attachment {name}: {e}")
                continue
            attachments.append({
                'name': name,
                'mimetype': raw.get('contentType') or 'application/octet-stream',
                'content': content,
                'is_inline': bool(raw.get('isInline')),
                'content_id': raw.get('contentId') or None,
            })
        return attachments

    # -------------------------------------------------------------------------
    # Graph -> normalized translation
    # -------------------------------------------------------------------------

    @api.model
    def _normalize_recipients(self, raw_recipients):
        """Turn Graph's [{'emailAddress': {...}}] into [{'email', 'name'}]."""
        recipients = []
        for raw in raw_recipients or []:
            address = raw.get('emailAddress') or {}
            email = address.get('address')
            if email:
                recipients.append({'email': email, 'name': address.get('name') or ''})
        return recipients

    @api.model
    def _normalize_message(self, raw):
        """Map a Graph message onto the normalized shape from the contract."""
        sender = self._normalize_recipients([raw.get('from')] if raw.get('from') else [])

        received = raw.get('receivedDateTime')
        date = None
        if received:
            try:
                date = datetime.fromisoformat(
                    received.replace('Z', '+00:00')
                ).replace(tzinfo=None)
            except ValueError:
                _logger.warning(f"[Graph API] Unparseable receivedDateTime: {received}")

        headers = {
            h['name'].lower(): h['value']
            for h in raw.get('internetMessageHeaders') or []
            if h.get('name')
        }

        body = raw.get('body') or {}
        body_html = body.get('content')
        if body_html is None:
            # List responses carry only a preview; get_message() has the body.
            body_html = raw.get('bodyPreview') or ''

        return {
            'provider_message_id': raw.get('id'),
            'message_id': raw.get('internetMessageId'),
            'thread_id': raw.get('conversationId'),
            'subject': raw.get('subject') or '',
            'from': sender[0] if sender else {'email': '', 'name': ''},
            'to': self._normalize_recipients(raw.get('toRecipients')),
            'cc': self._normalize_recipients(raw.get('ccRecipients')),
            'date': date,
            'body_html': body_html,
            'body_is_html': (body.get('contentType') or '').lower() == 'html',
            'has_attachments': bool(raw.get('hasAttachments')),
            'headers': headers,
            'is_read': bool(raw.get('isRead')),
        }

    # -------------------------------------------------------------------------
    # Raw Graph calls
    # -------------------------------------------------------------------------

    @api.model
    def _graph_fetch_messages(self, account, mailbox_email, folder='Inbox', since_datetime=None, top=50):
        """
        Fetch messages from a Microsoft mailbox folder via Graph API.

        Args:
            account: pan.mail.account holding OAuth tokens
            mailbox_email: Email address of the mailbox to fetch from
            folder: Graph folder name ('Inbox', 'SentItems', etc.)
            since_datetime: Only fetch messages received after this datetime
            top: Maximum number of messages to fetch

        Returns:
            list[dict]: List of raw message objects from Graph API
        """
        token = self.get_valid_token(account)

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
    def _graph_get_message(self, account, mailbox_email, message_id):
        """
        Get full message details including internet headers for threading.

        Args:
            account: pan.mail.account holding OAuth tokens
            mailbox_email: Email address of the mailbox
            message_id: Graph API message ID

        Returns:
            dict: Full message object with headers
        """
        token = self.get_valid_token(account)

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
    def _graph_get_attachments(self, account, mailbox_email, message_id):
        """
        Get attachments for a message.

        Args:
            account: pan.mail.account holding OAuth tokens
            mailbox_email: Email address of the mailbox
            message_id: Graph API message ID

        Returns:
            list[dict]: List of raw attachment objects
        """
        token = self.get_valid_token(account)

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
