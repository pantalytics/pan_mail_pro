# -*- coding: utf-8 -*-
import base64
import logging
import mimetypes
import re
import requests
import time
from datetime import datetime, timedelta
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from ... import encryption_utils
from ...mail_provider_client import (
    ERROR_NO_RECIPIENTS, FOLDER_ARCHIVE, FOLDER_DRAFTS, FOLDER_INBOX, FOLDER_JUNK,
    FOLDER_ROLES, FOLDER_SENT, FOLDER_TRASH,
    ERROR_THROTTLED,
    ThrottledError,
)

_logger = logging.getLogger(__name__)

# Microsoft's OAuth endpoints. The same two URLs for every tenant on the public
# cloud, which is why they are constants and not settings: they were config
# parameters on the settings page, where the only thing an admin could do with
# them was mistype one - and a mistyped endpoint cannot be fixed from a UI that
# needs the endpoint to log you in.
AUTH_URL = 'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize'
TOKEN_URL = 'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token'

# What Azure's AADSTS codes mean in terms of the three fields on the provider
# form. Azure's own error_description is accurate and unreadable ("AADSTS7000215:
# Invalid client secret provided. Ensure the secret being sent in the request is
# the client secret value..."), and it names no field an admin can see. The raw
# text is still shown underneath; this is the line that says which box to fix.
AADSTS_HINTS = {
    'AADSTS7000215': 'The Client Secret Value is wrong. In Azure, under '
                     'Certificates & secrets, copy the Value column -- it is '
                     'shown once, right after you create the secret. The '
                     'Secret ID is a different string and is not used here.',
    'AADSTS7000222': 'The Client Secret Value has expired. Create a new secret '
                     'in Azure under Certificates & secrets and paste its '
                     'Value here.',
    'AADSTS700016': 'Azure does not know this Application (client) ID in this '
                    'directory. Check it and the Directory (tenant) ID against '
                    "the app registration's Overview page.",
    'AADSTS90002': 'Azure does not know this Directory (tenant) ID. Copy it '
                   "from the app registration's Overview page -- it is a GUID, "
                   'not the client secret.',
    'AADSTS900023': 'That is not a Directory (tenant) ID. Copy it from the app '
                    "registration's Overview page -- it is a GUID, not the "
                    'client secret.',
    'AADSTS50011': 'The Callback URL on this form is not one of the redirect '
                   'URIs of the app registration. Paste it into Azure exactly '
                   'as it is shown here.',
}

# Rate limiting configuration
MAX_RETRIES = 3
# Longer than this, the cron does not wait: see _request_with_retry.
MAX_RETRY_AFTER_SECONDS = 15
INITIAL_BACKOFF_SECONDS = 2

# Attachment size threshold: Graph API allows max 3MB per direct attachment upload.
# Larger files must use the upload session API (supports up to 150MB).
DIRECT_ATTACHMENT_LIMIT = 3 * 1024 * 1024  # 3MB in bytes


# The threading headers as MAPI properties, for when Graph does not hand back
# `internetMessageHeaders` at all. That happens — the property is optional, it
# only ever appears under an explicit `$select`, and Exchange drops it on some
# items — and when it does, In-Reply-To and References go missing, the matcher
# has nothing to resolve, and a reply to our own mail lands on a subject guess.
# These three properties are on the item itself and are always there.
HEADER_EXTENDED_PROPERTIES = {
    'String 0x1035': 'message-id',    # PidTagInternetMessageId
    'String 0x1039': 'references',    # PidTagInternetReferences
    'String 0x1042': 'in-reply-to',   # PidTagInReplyToId
}
HEADER_EXTENDED_PROPERTIES_FILTER = ' or '.join(
    "id eq '%s'" % prop_id for prop_id in HEADER_EXTENDED_PROPERTIES
)


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
    supported_mailbox_types = ('personal', 'shared')
    # Azure answers "are these three fields the ones I issued?" on its own
    # token endpoint, with no user and no consent — see `test_credentials`.
    supports_credential_test = True

    # Odoo's folder roles -> Graph's well-known folder names. Graph accepts one
    # of these wherever it accepts a folder id, which is why a role needs no
    # lookup here: 'deleteditems' addresses the Trash of a mailbox in any
    # language.
    _FOLDER_MAP = {
        FOLDER_INBOX: 'Inbox',
        FOLDER_SENT: 'SentItems',
        FOLDER_DRAFTS: 'Drafts',
        FOLDER_TRASH: 'DeletedItems',
        FOLDER_ARCHIVE: 'Archive',
        FOLDER_JUNK: 'JunkEmail',
    }
    # The reverse, for reading a role off a folder Graph handed back. Keyed
    # lowercase because `wellKnownName` is always lowercase, where the names
    # above are the spelling Graph's own documentation uses in a URL — the two
    # are the same folder and Graph is case-insensitive about which you send.
    _WELL_KNOWN_ROLES = {name.lower(): role for role, name in _FOLDER_MAP.items()}

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
        return self.account_for_user(mailbox.owner_user_id)

    @api.model
    def _resolve_sending_user(self, mailbox, author_user=None):
        """Whose token sends from `mailbox`.

        Microsoft-specific by construction: every account here belongs to a
        person. Gmail answers the same question with a mailbox's own service
        account and no user at all, which is why `resolve_sending_account` is
        the contract method and this one is not.
        """
        if mailbox.mailbox_type == 'personal' or mailbox.is_notification_mailbox:
            return mailbox.owner_user_id
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
        """Translate a contract folder role into a Graph folder name.

        Roles only: `fetch_messages` reads the two folders the sync knows about
        and has no business being handed a folder id. Everything in the actions
        surface goes through `_graph_folder_id` instead.
        """
        self._check_folder_role(folder)
        return self._FOLDER_MAP[folder]

    @api.model
    def _graph_folder_id(self, folder):
        """A role or a folder id, resolved to whatever Graph takes in a URL.

        Graph makes this easy: a well-known name and a folder id are the same
        path segment, so a role maps and anything else is already an id.
        """
        if not folder:
            raise UserError(_('No mail folder given.'))
        return self._FOLDER_MAP.get(folder, folder)

    @api.model
    def _get_config_params(self):
        """Microsoft's own application registration — its `pan.mail.provider` row.

        Looked up by provider code, not by which provider is active: a
        database that switched to Gmail and back must find its old Azure
        registration exactly as it left it.
        """
        provider = self.env['pan.mail.provider'].sudo().search(
            [('provider', '=', 'outlook')], limit=1)
        client_secret = encryption_utils.decrypt_value(
            self.env, provider.client_secret_encrypted
        ) if provider.client_secret_encrypted else False

        return {
            'client_id': provider.client_id,
            'client_secret': client_secret,
            'tenant_id': provider.tenant_id,
        }

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

        auth_url = AUTH_URL.format(tenant=tenant_id)

        # The draft->send flow needs both halves, and they are different
        # permissions: Mail.ReadWrite creates and patches the draft,
        # Mail.Send performs POST /messages/{id}/send. Asking only for the
        # first left sending on borrowed consent — it works wherever an admin
        # happened to grant Mail.Send tenant-wide in Azure (which is what the
        # setup guide tells them to do), and 403s on a tenant that relies on
        # incremental consent. A permission granted in the portal but absent
        # from this list is not in the token; the .Shared pair is the same
        # story for a shared mailbox.
        scopes = [
            'openid',
            'profile',
            'email',
            'offline_access',
            'User.Read',
            'Mail.ReadWrite',
            'Mail.ReadWrite.Shared',
            'Mail.Send',
            'Mail.Send.Shared',
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
        self._refuse_when_neutralized()
        config = self._get_config_params()
        token_url = TOKEN_URL.format(tenant=config['tenant_id'])

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
            expiry = fields.Datetime.now() + timedelta(seconds=expires_in)

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
        token_url = TOKEN_URL.format(tenant=config['tenant_id'])

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
            expiry = fields.Datetime.now() + timedelta(seconds=expires_in)

            # sudo(): the token fields have groups='base.group_system'
            account.sudo().write({
                'access_token': token_data.get('access_token'),
                'refresh_token': token_data.get('refresh_token') or account.refresh_token,
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

            # invalid_client / unauthorized_client: the *application's* secret
            # is wrong or expired (AADSTS7000222 is the usual one). That is
            # the admin's problem, not this user's: clearing every user's
            # refresh token for it disconnected the whole company and made
            # everybody re-consent after the secret was fixed. Say what is
            # wrong, keep the tokens.
            if error_code in ('invalid_client', 'unauthorized_client'):
                aadsts = re.search(r'AADSTS\d+', error_description or '')
                hint = AADSTS_HINTS.get(aadsts.group(0)) if aadsts else None
                raise UserError(_(
                    'Microsoft refused the application credentials (%s). An '
                    'administrator checks the Client Secret under Settings, '
                    'Mail Pro. Your own connection is unchanged.') % (
                        hint or error_code))

            # invalid_grant: token revoked, expired, or user changed password.
            # This one is the user's, and only a new consent fixes it.
            permanent_errors = ('invalid_grant',)
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
        self._refuse_when_neutralized()
        # Check if token is expired or about to expire (5 min buffer)
        if account.token_expiry:
            buffer_time = fields.Datetime.now() + timedelta(minutes=5)
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
    def test_credentials(self):
        """Ask Azure whether the client id, secret and tenant are its own.

        The client-credentials grant is the one call that needs no user: Azure
        validates the three fields and hands back a token for the app itself.
        The token is thrown away — nothing here is authorized to read mail with
        it, and it is not meant to be. What is being tested is the registration,
        which is exactly what fails at the consent screen otherwise, an hour
        after the admin has emailed everybody to go and sign in.

        A tenant that grants the app no application permissions still issues
        this token, so a pass means "these three fields are right", not "every
        Graph permission is granted". Permissions are what `test_connection`
        finds out, once somebody has signed in.
        """
        self._refuse_when_neutralized()
        config = self._get_config_params()
        missing = [
            label for key, label in (
                ('client_id', _('Application (client) ID')),
                ('client_secret', _('Client Secret Value')),
                ('tenant_id', _('Directory (tenant) ID')),
            ) if not config[key]
        ]
        if missing:
            return {
                'success': False,
                'message': _('Fill in %s first.') % ', '.join(missing),
            }

        try:
            response = requests.post(
                TOKEN_URL.format(tenant=config['tenant_id']),
                data={
                    'client_id': config['client_id'],
                    'client_secret': config['client_secret'],
                    'scope': 'https://graph.microsoft.com/.default',
                    'grant_type': 'client_credentials',
                },
                timeout=10,
            )
        except requests.exceptions.RequestException as e:
            _logger.warning("[Graph API] Credential test could not reach Azure: %s", e)
            return {
                'success': False,
                'message': _('Could not reach Microsoft: %s') % e,
            }

        if response.status_code == 200 and response.json().get('access_token'):
            return {
                'success': True,
                'message': _(
                    'Azure accepted the Application (client) ID, Client Secret '
                    'Value and Directory (tenant) ID. '
                    'Users can now sign in; that is when their own mailbox '
                    'permissions are checked.'
                ),
            }
        return {'success': False, 'message': self._explain_credential_error(response)}

    @api.model
    def _explain_credential_error(self, response):
        """Turn Azure's token-endpoint refusal into a sentence about a field."""
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        description = (payload.get('error_description') or '').strip()
        # Azure appends a trace id, a correlation id and a timestamp to every
        # description. They matter to a support ticket and to nobody reading a
        # toast, and they push the sentence that does matter off the screen.
        headline = description.split('\r\n')[0].split('\n')[0].strip()

        code = re.search(r'AADSTS\d+', description)
        hint = AADSTS_HINTS.get(code.group(0)) if code else None
        if not hint and payload.get('error') == 'invalid_client':
            hint = ('Azure rejected the Application (client) ID or the '
                    'Client Secret Value.')
        _logger.warning(
            "[Graph API] Credential test rejected: %s",
            headline or response.status_code,
        )
        if hint and headline:
            return '%s\n\n%s' % (hint, headline)
        return hint or headline or (
            _('Microsoft returned HTTP %s.') % response.status_code)

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
        response = self._request_with_retry(
            'post', url, headers, timeout=30, json=attachment_dict, idempotent=False)
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
        session_response = self._request_with_retry(
            'post', session_url, headers, timeout=30, json=session_payload)
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
                reply_response = self._request_with_retry(
                    'post', f'{base_url}/{reply_to_provider_id}/createReply',
                    headers, timeout=30, idempotent=False,
                )
                reply_response.raise_for_status()
                draft_id = reply_response.json().get('id')
                if draft_id:
                    patch_response = self._request_with_retry(
                        'patch', f'{base_url}/{draft_id}', headers, timeout=30, json=message,
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

        # Through the retry helper: Exchange Online throttles bursts (a batch
        # of invitations from notifications@ is exactly one), and a 429 on
        # the draft used to park the mail in `exception`, which the queue
        # never retries.
        response = self._request_with_retry(
            'post', base_url, headers, timeout=30, json=message, idempotent=False)
        response.raise_for_status()
        return response.json()

    @api.model
    def _draft_content(self, mail_record, mailbox):
        """Turn one `mail.mail` into the Graph message body and its attachments.

        The half a send and a saved draft have in common, which is all of it
        bar the final POST: recipients, the X-Odoo-* loop guard, inline images
        rewritten to cid: parts, and the regular attachments kept out of the
        JSON so the 4MB payload limit is never the thing that fails.

        Returns:
            tuple: (message dict, attachments list, error) — `error` is a failed
            send result and is None when there is nothing wrong.
        """
        mailbox_email = mailbox.email
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
            # Distinguishable code so mail.mail.send() can skip+cancel this mail
            # (typically an internal notification to a user/partner without an
            # email address) instead of aborting the whole batch.
            return None, [], {
                'success': False,
                'error': 'No recipients specified (no email_to, recipient_ids, or email_cc with emails)',
                'error_code': ERROR_NO_RECIPIENTS,
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
        return message, all_attachments, None

    @api.model
    def send_email_via_graph(self, mail_record, mailbox, account, reply_context=None,
                             send=True):
        """
        Send email via Microsoft Graph API using Draft → Send flow.

        Creates a draft first (which returns internetMessageId and conversationId),
        then sends it. This enables proper duplicate detection and threading.

        Args:
            mail_record: mail.mail record to send
            mailbox: pan.mail.mailbox record to send from
            account: pan.mail.account holding a valid Microsoft OAuth token
            reply_context: optional threading hints (see the contract). Only
                `provider_message_id` is usable here — Graph will not accept
                In-Reply-To or References — and it selects the createReply
                draft flow instead of a plain one.
            send: stop after the draft is complete instead of sending it. This
                is what `save_draft` wants, and it is the same code path rather
                than a second one because Graph's send *is* draft-then-send:
                everything a draft needs was already built here, and the last
                POST is the only difference.

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

            message, all_attachments, content_error = self._draft_content(
                mail_record, mailbox)
            if content_error:
                return content_error

            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
            }

            recipient_emails = [r['emailAddress'].get('address', 'NO_ADDRESS')
                                for r in message.get('toRecipients') or []]
            cc_emails = [r['emailAddress'].get('address', 'NO_ADDRESS')
                         for r in message.get('ccRecipients') or []]
            _logger.info(f"[Graph API] {'Sending' if send else 'Drafting'} email from "
                         f"{mailbox_email} to {recipient_emails} cc {cc_emails}")

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

            if not send:
                _logger.info("[Graph API] Stored draft %s", draft_id)
                return {
                    'success': True,
                    'microsoft_draft_id': draft_id,
                    'microsoft_message_id': microsoft_message_id,
                    'microsoft_conversation_id': microsoft_conversation_id,
                }

            # Step 3: Send the draft
            send_url = f'https://graph.microsoft.com/v1.0/users/{graph_user_id}/messages/{draft_id}/send'
            send_response = self._request_with_retry(
                'post', send_url, headers, timeout=30, idempotent=False)
            send_response.raise_for_status()

            _logger.info("[Graph API] Successfully sent email %s", microsoft_message_id)

            return {
                'success': True,
                'microsoft_draft_id': draft_id,
                'microsoft_message_id': microsoft_message_id,
                'microsoft_conversation_id': microsoft_conversation_id,
            }

        except ThrottledError as e:
            # Not a failure: the mail waits for the pause Microsoft asked for.
            return {'success': False, 'error': str(e), 'error_code': ERROR_THROTTLED,
                    'retry_after': e.wait}
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
            if h.get('name') and h.get('value')
        }
        # Fill the gaps from the MAPI properties. Graph's header collection is
        # optional and sometimes simply absent; without In-Reply-To and
        # References the matcher has no chain to walk and falls back to a
        # subject guess, which is the bug this closes.
        extended = {
            HEADER_EXTENDED_PROPERTIES[prop['id']]: prop['value']
            for prop in raw.get('singleValueExtendedProperties') or []
            if prop.get('id') in HEADER_EXTENDED_PROPERTIES and prop.get('value')
        }
        for name, value in extended.items():
            headers.setdefault(name, value)

        body = raw.get('body') or {}
        body_html = body.get('content')
        if body_html is None:
            # List responses carry only a preview; get_message() has the body.
            body_html = raw.get('bodyPreview') or ''

        return {
            'provider_message_id': raw.get('id'),
            'message_id': raw.get('internetMessageId') or extended.get('message-id'),
            'thread_id': raw.get('conversationId'),
            'subject': raw.get('subject') or '',
            'from': sender[0] if sender else {'email': '', 'name': ''},
            'to': self._normalize_recipients(raw.get('toRecipients')),
            'cc': self._normalize_recipients(raw.get('ccRecipients')),
            'date': date,
            'body_html': body_html,
            'body_is_html': (body.get('contentType') or '').lower() == 'html',
            'has_attachments': bool(raw.get('hasAttachments')),
            'headers': self.normalize_headers(headers),
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
            # Belt and braces on the threading headers; see
            # HEADER_EXTENDED_PROPERTIES.
            '$expand': 'singleValueExtendedProperties($filter=%s)'
                       % HEADER_EXTENDED_PROPERTIES_FILTER,
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

    def _request_with_retry(self, method, url, headers, timeout=30, idempotent=True, **kwargs):
        """
        Execute HTTP request with rate limiting and exponential backoff.

        `idempotent=False` is for a request the server may have carried out
        before the answer was lost (sending a message, creating a draft or
        an attachment): it is retried on a 429 only, because a 429 means
        the request was refused. A timeout on `/send` retried three times
        is a customer mailed four times.

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
                    try:
                        wait_time = int(retry_after) if retry_after else backoff
                    except ValueError:
                        # An HTTP-date rather than seconds: back off, do not crash.
                        wait_time = backoff
                    if wait_time > MAX_RETRY_AFTER_SECONDS:
                        # Sleeping this long inside the one-minute cron holds
                        # every other mailbox's turn hostage and, past the
                        # worker's time limit, rolls the whole run back. The
                        # mailbox records the throttle instead and the next
                        # run tries again.
                        raise ThrottledError(_(
                            'Microsoft asked to wait %s seconds before more requests '
                            'for this mailbox. Try again in a minute.') % wait_time, wait_time)

                    if attempt < MAX_RETRIES:
                        _logger.warning(f"[Graph API] Rate limited (429), waiting {wait_time}s before retry {attempt + 1}/{MAX_RETRIES}")
                        time.sleep(wait_time)
                        backoff *= 2  # Exponential backoff
                        continue
                    else:
                        response.raise_for_status()  # Raise on final attempt

                # Check for other server errors that might be transient
                if response.status_code in (500, 502, 503, 504) and attempt < MAX_RETRIES and idempotent:
                    _logger.warning(f"[Graph API] Server error ({response.status_code}), retrying in {backoff}s ({attempt + 1}/{MAX_RETRIES})")
                    time.sleep(backoff)
                    backoff *= 2
                    continue

                return response

            except requests.exceptions.Timeout as e:
                last_exception = e
                if attempt < MAX_RETRIES and idempotent:
                    _logger.warning(f"[Graph API] Request timeout, retrying in {backoff}s ({attempt + 1}/{MAX_RETRIES})")
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                raise

            except requests.exceptions.ConnectionError as e:
                last_exception = e
                if attempt < MAX_RETRIES and idempotent:
                    _logger.warning(f"[Graph API] Connection error, retrying in {backoff}s ({attempt + 1}/{MAX_RETRIES})")
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                raise

        # Should not reach here, but just in case
        if last_exception:
            raise last_exception
        raise requests.exceptions.RequestException("Max retries exceeded")

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

    # -------------------------------------------------------------------------
    # Mailbox actions — contract implementation
    #
    # Folders, search, marking, filing and drafts. Everything here addresses a
    # mailbox as /users/{email}/..., the same delegated token the sync and the
    # send already use, and every folder argument goes through
    # `_graph_folder_id` so a role and a folder id are interchangeable.
    # -------------------------------------------------------------------------

    def _graph_call(self, account, method, path, **kwargs):
        """One authenticated Graph call against the mailbox, JSON in and out.

        Everything below goes through it so that retry, rate limiting and the
        error message an admin actually reads are written once.
        """
        token = self.get_valid_token(account)
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        }
        url = f'https://graph.microsoft.com/v1.0{path}'
        try:
            response = self._request_with_retry(method, url, headers=headers, **kwargs)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            detail = self._extract_graph_error(e)
            _logger.error('[Graph API] %s %s failed: %s', method.upper(), path, detail)
            raise UserError(_('Microsoft 365 refused the request: %s') % detail)
        if response.status_code == 204 or not (response.content or b'').strip():
            return {}
        try:
            return response.json()
        except ValueError:
            return {}

    # ---- folders ------------------------------------------------------------

    @api.model
    def list_folders(self, account, mailbox):
        """Every folder in the mailbox, children included (see contract).

        Graph lists one level at a time, so the children are walked rather than
        asked for: `childFolderCount` says where to descend and nowhere else,
        which keeps this to one call per folder that actually has children.

        The role comes from `wellKnownName` when Graph fills it in, and from
        the folder's position among the well-known folders when it does not —
        never from the display name, which is localized.
        """
        folders = []
        self._walk_folders(account, mailbox, '/mailFolders', folders, depth=0)
        return folders

    def _walk_folders(self, account, mailbox, path, out, depth, prefix=''):
        # Deep enough for any mailbox a person made by hand; a cycle or a
        # pathological hierarchy stops here rather than in a timeout.
        if depth > 6:
            return
        data = self._graph_call(
            account, 'get', f'/users/{mailbox.email}{path}',
            params={'$top': 200},
        )
        for raw in data.get('value') or []:
            folder_id = raw.get('id')
            if not folder_id:
                continue
            name = raw.get('displayName') or ''
            out.append({
                'id': folder_id,
                'name': prefix + name if prefix else name,
                'role': self._folder_role(raw),
            })
            if raw.get('childFolderCount'):
                self._walk_folders(
                    account, mailbox, f'/mailFolders/{folder_id}/childFolders',
                    out, depth + 1, prefix=f'{prefix}{name}/',
                )

    @api.model
    def _folder_role(self, raw):
        """Which contract role a Graph folder claims, if any."""
        well_known = (raw.get('wellKnownName') or '').lower()
        return self._WELL_KNOWN_ROLES.get(well_known)

    @api.model
    def _folder_id_for_role(self, account, mailbox, role):
        """The mailbox's own id for a role's well-known folder."""
        self._check_folder_role(role)
        data = self._graph_call(
            account, 'get',
            f'/users/{mailbox.email}/mailFolders/{self._FOLDER_MAP[role]}',
            params={'$select': 'id,displayName'},
        )
        return data.get('id')

    @api.model
    def _refuse_if_load_bearing(self, account, mailbox, folder, verb):
        """Refuse the inbox and any folder holding a role.

        Every one of them is somewhere mail is filed without anybody asking —
        the Sent copy, the Trash a delete lands in, the Drafts a review sits
        in. Renaming or deleting one breaks that quietly and the next call
        cannot say why, so it is refused by name rather than gated behind a
        confirmation nobody can judge.
        """
        folder_id = self._graph_folder_id(folder)
        for role in FOLDER_ROLES:
            if folder in (role, self._FOLDER_MAP[role]):
                raise UserError(_(
                    'The %(role)s folder cannot be %(verb)s: mail is filed there '
                    'without anyone asking, including by Microsoft 365 itself.',
                    role=role, verb=verb,
                ))
            if self._folder_id_for_role(account, mailbox, role) == folder_id:
                raise UserError(_(
                    'That is this mailbox\'s %(role)s folder, so it cannot be '
                    '%(verb)s: mail is filed there without anyone asking, '
                    'including by Microsoft 365 itself.',
                    role=role, verb=verb,
                ))

    @api.model
    def create_folder(self, account, mailbox, name, parent=None):
        """Create a folder, optionally inside `parent` (see contract)."""
        name = (name or '').strip()
        if not name:
            raise UserError(_('A folder needs a name.'))
        if parent:
            parent_id = self._graph_folder_id(parent)
            path = f'/users/{mailbox.email}/mailFolders/{parent_id}/childFolders'
        else:
            path = f'/users/{mailbox.email}/mailFolders'
        existing = self._graph_call(account, 'get', path, params={'$top': 200})
        for raw in existing.get('value') or []:
            if (raw.get('displayName') or '').lower() == name.lower():
                # Asked for, and already true. Not a failure.
                return raw.get('id'), False
        created = self._graph_call(account, 'post', path, json={'displayName': name})
        return created.get('id'), True

    @api.model
    def rename_folder(self, account, mailbox, folder, new_name):
        """Rename a folder, keeping it where it is (see contract)."""
        new_name = (new_name or '').strip()
        if not new_name:
            raise UserError(_('A folder needs a name.'))
        self._refuse_if_load_bearing(account, mailbox, folder, _('renamed'))
        folder_id = self._graph_folder_id(folder)
        data = self._graph_call(
            account, 'patch', f'/users/{mailbox.email}/mailFolders/{folder_id}',
            json={'displayName': new_name},
        )
        return data.get('id') or folder_id

    @api.model
    def delete_folder(self, account, mailbox, folder):
        """Delete an EMPTY folder (see contract).

        Graph deletes the folder's mail with it, exactly as IMAP does, so the
        emptiness check is not a courtesy — it is the whole reason the caller
        can trust that nothing here loses mail.
        """
        self._refuse_if_load_bearing(account, mailbox, folder, _('deleted'))
        folder_id = self._graph_folder_id(folder)
        raw = self._graph_call(
            account, 'get', f'/users/{mailbox.email}/mailFolders/{folder_id}',
            params={'$select': 'id,displayName,totalItemCount,childFolderCount'},
        )
        if raw.get('childFolderCount'):
            raise UserError(_(
                '"%(name)s" still has %(count)s folder(s) inside it. Delete those '
                'first, innermost one first.',
                name=raw.get('displayName') or folder, count=raw['childFolderCount'],
            ))
        if raw.get('totalItemCount'):
            raise UserError(_(
                '"%(name)s" still holds %(count)s message(s), and deleting a folder '
                'takes its mail with it. Empty it first: delete the messages (they '
                'go to the Deleted Items folder and can be fished back out) or move '
                'them somewhere else.',
                name=raw.get('displayName') or folder, count=raw['totalItemCount'],
            ))
        self._graph_call(account, 'delete', f'/users/{mailbox.email}/mailFolders/{folder_id}')
        return folder_id

    # ---- searching ----------------------------------------------------------

    @api.model
    def search_messages(self, account, mailbox, folder=FOLDER_INBOX, query=None,
                        sender=None, unread_only=False, flagged_only=False,
                        has_attachment=False, limit=50):
        """Search one folder, newest first (see contract).

        Graph refuses `$search` and `$filter` in the same request, which is the
        one thing that shapes this method. With free text we go to `$search`
        and hand it the terms KQL understands (`from:`, `hasAttachment:`);
        without it we go to `$filter`, which is the only way to get
        `$orderby receivedDateTime desc` at all — `$search` orders by
        relevance and will not be told otherwise.

        What KQL cannot express (read and flag state) is verified here, on the
        summaries Graph returned: narrowing in Python after the fact is exact,
        and the alternative is a filter the server would refuse to combine.
        """
        limit = max(1, min(int(limit or 50), 200))
        folder_id = self._graph_folder_id(folder)
        select = ('id,internetMessageId,conversationId,subject,from,toRecipients,'
                  'ccRecipients,receivedDateTime,bodyPreview,hasAttachments,isRead,flag')
        params = {'$top': limit, '$select': select}

        if query:
            terms = [str(query).strip()]
            if sender:
                terms.append('from:%s' % sender)
            if has_attachment:
                terms.append('hasAttachment:true')
            params['$search'] = '"%s"' % ' '.join(terms).replace('"', '')
        else:
            clauses = []
            if sender:
                clauses.append("from/emailAddress/address eq '%s'" % str(sender).replace("'", "''"))
            if has_attachment:
                clauses.append('hasAttachments eq true')
            if unread_only:
                clauses.append('isRead eq false')
            if clauses:
                params['$filter'] = ' and '.join(clauses)
            params['$orderby'] = 'receivedDateTime desc'

        data = self._graph_call(
            account, 'get',
            f'/users/{mailbox.email}/mailFolders/{folder_id}/messages',
            params=params,
        )
        messages = []
        for raw in data.get('value') or []:
            if unread_only and raw.get('isRead'):
                continue
            if flagged_only and (raw.get('flag') or {}).get('flagStatus') != 'flagged':
                continue
            messages.append(self._normalize_message(raw))
        return messages[:limit]

    # ---- message state ------------------------------------------------------

    @api.model
    def set_seen(self, account, mailbox, provider_message_ids, seen=True):
        """Mark messages read or unread (see contract)."""
        return self._patch_messages(account, mailbox, provider_message_ids,
                                    {'isRead': bool(seen)})

    @api.model
    def set_flagged(self, account, mailbox, provider_message_ids, flagged=True):
        """Star messages or unstar them (see contract)."""
        status = 'flagged' if flagged else 'notFlagged'
        return self._patch_messages(account, mailbox, provider_message_ids,
                                    {'flag': {'flagStatus': status}})

    def _patch_messages(self, account, mailbox, provider_message_ids, payload):
        """PATCH the same body onto each message. Returns how many were changed.

        One request per message on purpose: Graph's `$batch` would halve the
        round trips and doubles the failure modes (a partial batch reports 200
        with per-item errors inside), and marking a handful of messages is not
        where this module spends its time.
        """
        count = 0
        for message_id in self._as_id_list(provider_message_ids):
            self._graph_call(
                account, 'patch', f'/users/{mailbox.email}/messages/{message_id}',
                json=payload,
            )
            count += 1
        return count

    @api.model
    def _as_id_list(self, provider_message_ids):
        """One id or many, always a list."""
        if not provider_message_ids:
            return []
        if isinstance(provider_message_ids, str):
            return [provider_message_ids]
        return [i for i in provider_message_ids if i]

    # ---- filing -------------------------------------------------------------

    @api.model
    def move_messages(self, account, mailbox, provider_message_ids, destination):
        """Move messages into `destination` (see contract).

        Graph mints a new message id on a move, which is why the new ones come
        back: a caller holding the old id is holding a reference to a message
        that is not there any more.
        """
        destination_id = self._graph_folder_id(destination)
        moved = []
        for message_id in self._as_id_list(provider_message_ids):
            data = self._graph_call(
                account, 'post', f'/users/{mailbox.email}/messages/{message_id}/move',
                json={'destinationId': destination_id},
            )
            moved.append(data.get('id') or message_id)
        return moved

    @api.model
    def delete_messages(self, account, mailbox, provider_message_ids):
        """Move messages to Deleted Items (see contract).

        Not `DELETE /messages/{id}`, which on Graph is a real delete once the
        message is already in Deleted Items — and the point of this method is
        that it is the one mail write a person can undo.
        """
        message_ids = self._as_id_list(provider_message_ids)
        trash_id = self._folder_id_for_role(account, mailbox, FOLDER_TRASH)
        for message_id in message_ids:
            raw = self._graph_call(
                account, 'get', f'/users/{mailbox.email}/messages/{message_id}',
                params={'$select': 'id,parentFolderId'},
            )
            if raw.get('parentFolderId') == trash_id:
                raise UserError(_(
                    'Those messages are already in Deleted Items. Emptying it is not '
                    'something Mail Pro does for you: move them somewhere else, or '
                    'delete them in Outlook.'
                ))
        return self.move_messages(account, mailbox, message_ids, trash_id), trash_id

    # ---- drafts -------------------------------------------------------------

    @api.model
    def save_draft(self, mail_record, mailbox, account, reply_context=None):
        """Store `mail_record` in Drafts without sending (see contract)."""
        result = self.send_email_via_graph(
            mail_record=mail_record, mailbox=mailbox, account=account,
            reply_context=reply_context, send=False,
        )
        if not result.get('success'):
            raise UserError(_('Could not save the draft: %s') % (result.get('error') or ''))
        return result.get('microsoft_draft_id')

    @api.model
    def update_draft(self, mail_record, mailbox, account, provider_message_id,
                     reply_context=None):
        """Replace a stored draft's content, keeping the draft (see contract).

        PATCHed in place rather than deleted and rebuilt, which is what keeps
        the threading `reply_context` is allowed to be silent about: the draft
        Graph made with `createReply` carries In-Reply-To, References and the
        conversationId, and none of those can be set on a draft made from
        scratch. The attachments are the exception — Graph has no way to
        replace a collection, so they are removed and re-added.
        """
        payload, attachments, error = self._draft_content(mail_record, mailbox)
        if error:
            raise UserError(_('Could not update the draft: %s') % error)
        base = f'/users/{mailbox.email}/messages/{provider_message_id}'
        self._graph_call(account, 'patch', base, json=payload)

        existing = self._graph_call(account, 'get', f'{base}/attachments',
                                    params={'$select': 'id'})
        for raw in existing.get('value') or []:
            if raw.get('id'):
                self._graph_call(account, 'delete', f'{base}/attachments/{raw["id"]}')

        token = self.get_valid_token(account)
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        for att in attachments:
            raw_bytes = base64.b64decode(att['contentBytes'])
            if len(raw_bytes) < DIRECT_ATTACHMENT_LIMIT:
                self._add_attachment_to_draft(headers, mailbox.email,
                                              provider_message_id, att)
            else:
                self._upload_large_attachment(
                    headers, mailbox.email, provider_message_id,
                    name=att['name'], content_type=att['contentType'],
                    raw_bytes=raw_bytes, is_inline=att.get('isInline', False),
                )
        return provider_message_id

    @api.model
    def send_draft(self, account, mailbox, provider_message_id):
        """Send a stored draft as it stands (see contract).

        Graph sends the draft itself and files it in Sent Items, so there is no
        draft left to remove afterwards — the one place this provider gets the
        never-fatal cleanup for free.
        """
        base = f'/users/{mailbox.email}/messages/{provider_message_id}'
        # Read the ids before sending: the draft is gone from Drafts the moment
        # it goes out, and these are the handles dedup and threading key on.
        raw = self._graph_call(account, 'get', base,
                               params={'$select': 'id,internetMessageId,conversationId'})
        self._graph_call(account, 'post', f'{base}/send')
        return {
            'success': True,
            'error': None,
            'error_code': None,
            'message_id': raw.get('internetMessageId'),
            'thread_id': raw.get('conversationId'),
        }
