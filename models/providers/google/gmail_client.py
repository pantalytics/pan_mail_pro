# -*- coding: utf-8 -*-
"""Gmail REST API client — the Google counterpart of microsoft/graph_client.py.

Same shape, different wire. Raw `requests` against googleapis.com, no
google-api-python-client dependency, so it installs like the rest of the module.
This file is the ONLY place Gmail's JSON and OAuth details are understood; it
normalizes on the way out and no caller above ever sees a Gmail key.

Why REST and not IMAP: the decision (2026-07-24) chose the native Gmail API over
IMAP/SMTP. The history API replaces IMAP's UIDVALIDITY/UIDNEXT state machine, and
draft/send hands back a real Message-ID and threadId for threading and dedup —
the same seam the Graph client already gives us.
"""
import base64
import collections
import logging
import requests
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses, parseaddr

from odoo import models, fields, api, _
from odoo.exceptions import UserError
from ... import encryption_utils
from ...mail_provider_client import (
    ERROR_NO_RECIPIENTS, FOLDER_ARCHIVE, FOLDER_DRAFTS, FOLDER_INBOX, FOLDER_JUNK,
    FOLDER_SENT, FOLDER_TRASH,
)
from .. import mime_utils

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

# Paging for the message list. 500 is Gmail's own maximum for `maxResults`, and
# the page cap exists only so a pathological backlog cannot hold the one-minute
# cron open; hitting it is logged, never swallowed. See `_gmail_list_ids`.
GMAIL_LIST_PAGE_SIZE = 500
GMAIL_LIST_MAX_PAGES = 100


class GoogleGmailClient(models.AbstractModel):
    """Google Workspace implementation of the `mail.provider.client` contract.

    Everything Gmail-specific lives here: the REST endpoints, the OAuth
    endpoints and scopes, the RFC822 send flow, and the shapes of Gmail's JSON
    payloads. Callers see only the normalized structures documented in
    `mail_provider_client.py`.
    """
    _name = 'google.gmail.client'
    _inherit = 'mail.provider.client'
    _description = 'Gmail REST API Client'

    # Gmail has no SendAs-with-your-own-token equivalent: a shared address is a
    # real Workspace account, authorized once, with its own credentials.
    supports_shared_mailbox = False
    supports_delegation = True
    supported_mailbox_types = ('personal', 'shared')

    # Odoo's folder roles -> Gmail's system labels.
    #
    # FOLDER_ARCHIVE has no entry, and that is not an omission: Gmail has no
    # Archive label. Archiving a message there means removing INBOX from it,
    # so the role is the *absence* of a label, which `_gmail_label_id` refuses
    # to hand back and `move_messages` and `search_messages` handle by name.
    _LABEL_MAP = {
        FOLDER_INBOX: 'INBOX',
        FOLDER_SENT: 'SENT',
        FOLDER_DRAFTS: 'DRAFT',
        FOLDER_TRASH: 'TRASH',
        FOLDER_JUNK: 'SPAM',
    }
    _LABEL_ROLES = {label: role for role, label in _LABEL_MAP.items()}
    # The three labels that say WHERE a message is, as opposed to what it is
    # about. Gmail lets a message carry several labels but only one of these,
    # so a move is "take off whichever of these it has, put on the target".
    _LOCATION_LABELS = ('INBOX', 'TRASH', 'SPAM')

    @api.model
    def provider_code(self):
        return 'gmail'

    @api.model
    def provider_label(self):
        return 'Gmail'

    @api.model
    def _gmail_label(self, folder):
        """Translate a contract folder role into a Gmail system label.

        Roles only, and only the two the sync reads. Everything in the actions
        surface goes through `_gmail_label_id`, which also takes a label id.
        """
        self._check_folder_role(folder)
        try:
            return self._LABEL_MAP[folder]
        except KeyError:
            raise UserError(_(
                'Gmail has no "%s" folder.'
            ) % folder)

    @api.model
    def _gmail_label_id(self, folder):
        """A role or a label id, resolved to the label id Gmail takes.

        Refuses FOLDER_ARCHIVE: there is no label to name, and answering with
        INBOX or with nothing would both file mail somewhere the caller did not
        ask for. The two methods that can honour an archive do it themselves.
        """
        if not folder:
            raise UserError(_('No mail folder given.'))
        if folder == FOLDER_ARCHIVE:
            raise UserError(_(
                'Gmail has no Archive folder: archiving a message means taking it '
                'out of the Inbox. Move it to another label, or archive it with '
                'move_messages().'
            ))
        return self._LABEL_MAP.get(folder, folder)

    # -------------------------------------------------------------------------
    # Credentials
    # -------------------------------------------------------------------------
    @api.model
    def account_for_user(self, user):
        return self.env['pan.mail.account']._for_user(user, self.provider_code())

    @api.model
    def resolve_sending_account(self, mailbox, author_user=None):
        """Which Google credentials send from `mailbox`.

        Unlike Microsoft — where a shared mailbox is sent with the author's own
        token via SendAs — a Gmail shared mailbox is its OWN Workspace account:
        one refresh token, no user behind it, authorized once. So a shared
        mailbox sends with its service account and personal/notification
        mailboxes send with the owner's. The author never enters into it, which
        is exactly why the contract hands it over rather than deciding here.
        """
        return self.resolve_receiving_account(mailbox)

    @api.model
    def resolve_receiving_account(self, mailbox):
        if mailbox.mailbox_type == 'shared':
            return self._service_account(mailbox)
        return self.account_for_user(mailbox.owner_user_id)

    @api.model
    def _service_account(self, mailbox):
        """The mailbox's own Gmail account — user_id null, keyed on the address."""
        return self.env['pan.mail.account'].sudo().with_context(active_test=False).search([
            ('provider', '=', self.provider_code()),
            ('user_id', '=', False),
            ('email', '=', mailbox.email),
        ], limit=1)

    # -------------------------------------------------------------------------
    # Configuration
    # -------------------------------------------------------------------------
    @api.model
    def _get_config_params(self):
        """Google's own application registration — its `pan.mail.provider` row.

        Looked up by provider code, not by which provider is active — see the
        same comment on Microsoft's `_get_config_params()`. The secret is
        Fernet-encrypted at rest like Microsoft's.
        """
        provider = self.env['pan.mail.provider'].sudo().search(
            [('provider', '=', 'gmail')], limit=1)
        client_secret = encryption_utils.decrypt_value(
            self.env, provider.client_secret_encrypted
        ) if provider.client_secret_encrypted else False

        return {
            'client_id': provider.client_id,
            'client_secret': client_secret,
        }

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
    def _exchange_code_for_tokens(self, authorization_code, redirect_uri):
        """Trade an authorization code for access + refresh tokens."""
        self._refuse_when_neutralized()
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
                'token_expiry': fields.Datetime.now() + timedelta(seconds=expires_in),
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
                'token_expiry': fields.Datetime.now() + timedelta(seconds=expires_in),
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
        self._refuse_when_neutralized()
        if account.token_expiry:
            if account.token_expiry <= fields.Datetime.now() + timedelta(minutes=5):
                _logger.info('[Gmail API] Token expired for %s, refreshing...', account.email)
                return self.refresh_access_token(account)

        if not account.access_token:
            raise UserError(_('No access token available. Please connect your Google account.'))
        return account.access_token

    # -------------------------------------------------------------------------
    # Sending
    # -------------------------------------------------------------------------
    @api.model
    def send_message(self, mail_record, mailbox, account, reply_context=None):
        """Send one mail.mail via the Gmail REST API.

        Gmail takes a base64url-encoded RFC822 message, not a JSON body like
        Graph. We build the MIME ourselves, which means we set the Message-ID
        rather than receive it — so we return the one we generated. Gmail
        respects a supplied Message-ID, and having it up front is what lets
        dedup and reply-threading key on it exactly as they do for Microsoft.

        Returns the normalized send result from the contract, so this client
        and the Graph one are interchangeable to the caller.
        """
        token = self.get_valid_token(account)
        mailbox_email = mailbox.email
        reply_context = reply_context or {}

        to_addrs = mime_utils.collect_recipients(mail_record.email_to, mail_record.recipient_ids)
        cc_addrs = mime_utils.collect_recipients(mail_record.email_cc)
        if not to_addrs and not cc_addrs:
            return {
                'success': False,
                'error': 'No recipients specified (no email_to, recipient_ids, or email_cc with emails)',
                # Same distinguishable code Graph returns, so mail.mail.send()
                # skips+cancels this one instead of aborting the batch.
                'error_code': ERROR_NO_RECIPIENTS,
            }

        message_id = mime_utils.new_message_id(mailbox_email)
        msg = mime_utils.build_message(
            mail_record, mailbox_email, to_addrs, cc_addrs, message_id,
            reply_context=reply_context)

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        payload = {'raw': raw}
        # Only claim the thread when the headers back it up — a threadId without
        # a matching In-Reply-To is rejected by Gmail, not silently accepted.
        if reply_context.get('thread_id') and reply_context.get('in_reply_to'):
            payload['threadId'] = reply_context['thread_id']

        url = 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send'
        try:
            response = requests.post(
                url,
                headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            sent = response.json()
        except requests.exceptions.RequestException as e:
            return {'success': False, 'error': self._error_detail(e),
                    'error_code': self._error_code(e)}

        return {
            'success': True,
            'error': None,
            'error_code': None,
            'message_id': message_id,           # the RFC5322 Message-ID we set
            'thread_id': sent.get('threadId'),  # Gmail's thread handle
        }

    # -------------------------------------------------------------------------
    # Receiving — contract implementation
    #
    # The public methods here satisfy `mail.provider.client` and hand back
    # normalized dicts. The `_gmail_*` helpers underneath are the only code that
    # touches Gmail's payload shapes.
    # -------------------------------------------------------------------------
    def _api_get(self, account, url, params=None):
        token = self.get_valid_token(account)
        try:
            response = requests.get(
                url,
                headers={'Authorization': f'Bearer {token}'},
                params=params or {},
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            _logger.error('[Gmail API] GET %s failed: %s', url, self._error_detail(e))
            raise UserError(_('Gmail request failed: %s') % self._error_detail(e))

    @api.model
    def _gmail_list_ids(self, account, label, after_epoch=None, limit=50):
        """The OLDEST `limit` message ids in a label.

        Gmail's list returns only {id, threadId} - no date, no Message-ID - so
        the caller must fetch metadata per id to build a preview. `after_epoch`
        maps to the `after:` search operator; overlap is harmless because the
        processor dedups on message_id before doing anything expensive.

        The oldest, not the newest, and that is the whole reason this paginates.
        Gmail's list offers no ordering control: it answers newest first and
        pages with `nextPageToken`. Taking its first page therefore takes the
        newest `limit` of a backlog — and since the caller advances its cursor
        to the newest message of the batch it received, every older message is
        stepped over and never asked for again. A mailbox 1000 messages behind
        would sync 200 and lose 800, silently. So the pages are walked to the
        end and only the last `limit` ids are kept; ids are cheap, and only the
        ids that survive get a metadata fetch.

        Only when there is a cursor to be behind, though. Without `after_epoch`
        the window is the entire mailbox, and the one caller in that position is
        the first-sync connection test, which asks for a single id and throws
        the answer away. Paging a whole mailbox to answer it would turn one
        request into a hundred, so an uncursored call takes the first page and
        stops.
        """
        params = {
            'labelIds': label,
            'maxResults': GMAIL_LIST_PAGE_SIZE if after_epoch else limit,
            # Chats are not mail; excluding them keeps the sync to real email.
            'q': '-in:chats',
        }
        if not after_epoch:
            return self._api_get(
                account, 'https://gmail.googleapis.com/gmail/v1/users/me/messages',
                params).get('messages') or []
        params['q'] += f' after:{int(after_epoch)}'

        # Pages arrive newest first, so the last `limit` ids appended are the
        # oldest. A deque keeps memory flat no matter how deep the backlog is.
        oldest = collections.deque(maxlen=limit)
        page_token = None
        for _page in range(GMAIL_LIST_MAX_PAGES):
            data = self._api_get(
                account, 'https://gmail.googleapis.com/gmail/v1/users/me/messages',
                dict(params, pageToken=page_token) if page_token else params)
            oldest.extend(data.get('messages') or [])
            page_token = data.get('nextPageToken')
            if not page_token:
                return list(oldest)

        # Never silently: the batch below is the oldest of what was listed, not
        # the oldest that exists, so this run does skip mail. Saying so is the
        # difference between a slow sync and a mystery.
        _logger.warning(
            '[Gmail API] Backlog in %s exceeds %d messages; syncing the oldest '
            'of the first %d listed. Older mail may be skipped — move the '
            "mailbox's sync start date forward or re-run until it catches up.",
            label, GMAIL_LIST_PAGE_SIZE * GMAIL_LIST_MAX_PAGES,
            GMAIL_LIST_PAGE_SIZE * GMAIL_LIST_MAX_PAGES,
        )
        return list(oldest)

    @api.model
    def _gmail_get_message(self, account, gmail_id, fmt='full', headers=None):
        """Fetch one message. fmt='metadata' + headers=[...] for a cheap preview,
        fmt='full' for the body and attachments."""
        params = {'format': fmt}
        if fmt == 'metadata' and headers:
            params['metadataHeaders'] = headers
        return self._api_get(
            account,
            f'https://gmail.googleapis.com/gmail/v1/users/me/messages/{gmail_id}',
            params,
        )

    @api.model
    def _gmail_get_attachment_data(self, account, gmail_id, attachment_id):
        """Fetch and decode one attachment body (Gmail hands it back base64url)."""
        data = self._api_get(
            account,
            f'https://gmail.googleapis.com/gmail/v1/users/me/messages/{gmail_id}'
            f'/attachments/{attachment_id}',
            {},
        )
        raw = data.get('data')
        return base64.urlsafe_b64decode(raw) if raw else b''

    @api.model
    def fetch_messages(self, account, mailbox, folder=FOLDER_INBOX,
                       since_datetime=None, limit=50):
        """List messages in a folder, oldest first (see contract).

        Gmail's list gives only ids, so each preview costs a metadata fetch to
        recover the Message-ID and date. That is more calls than Graph (whose
        list carries both), but still far cheaper than the full body, which the
        processor fetches only for messages that survive its skip checks.
        """
        after_epoch = (since_datetime.replace(tzinfo=timezone.utc).timestamp()
                       if since_datetime else None)
        entries = self._gmail_list_ids(
            account, self._gmail_label(folder), after_epoch, limit)

        messages = []
        for entry in entries:
            raw = self._gmail_get_message(
                account, entry['id'], fmt='metadata',
                headers=['Message-Id', 'Subject', 'From', 'To', 'Cc'])
            messages.append(self._normalize_message(raw))
        # Gmail returns newest-first; the sync cursor advances on ascending date.
        messages.sort(key=lambda m: m['date'] or datetime.min)
        return messages

    @api.model
    def get_message(self, account, mailbox, provider_message_id):
        """Fetch one message in full, including headers and body."""
        raw = self._gmail_get_message(account, provider_message_id, fmt='full')
        return self._normalize_message(raw)

    @api.model
    def get_message_attachments(self, account, mailbox, provider_message_id):
        """Return normalized attachments; never raises (see contract)."""
        attachments = []
        try:
            raw = self._gmail_get_message(account, provider_message_id, fmt='full')
            for part in self._walk(raw.get('payload') or {}):
                filename = part.get('filename')
                if not filename:
                    continue
                body = part.get('body') or {}
                if body.get('data'):
                    content = base64.urlsafe_b64decode(body['data'])
                elif body.get('attachmentId'):
                    content = self._gmail_get_attachment_data(
                        account, provider_message_id, body['attachmentId'])
                else:
                    continue
                if not content:
                    continue
                part_headers = self._headers_dict(part)
                content_id = (part_headers.get('content-id') or '').strip('<>')
                # Inline if the sender said so or gave it a Content-Id to
                # reference from the body — the same test Graph applies.
                disposition = (part_headers.get('content-disposition') or '').lower()
                is_inline = 'inline' in disposition or bool(content_id)
                attachments.append({
                    'name': filename,
                    'mimetype': part.get('mimeType') or 'application/octet-stream',
                    'content': content,
                    'is_inline': is_inline,
                    'content_id': content_id or None,
                })
        except Exception as e:
            # Contract: an attachment failure must not sink the message.
            _logger.warning('[Gmail API] Could not fetch attachments for %s: %s',
                            provider_message_id, e)
            return []
        return attachments

    # -------------------------------------------------------------------------
    # Gmail -> normalized translation
    # -------------------------------------------------------------------------
    @api.model
    def _normalize_message(self, raw):
        """Map a Gmail message onto the normalized shape from the contract."""
        payload = raw.get('payload') or {}
        headers = self._headers_dict(payload)
        body_html, body_is_html = self._extract_body(payload)
        label_ids = raw.get('labelIds') or []

        return {
            'provider_message_id': raw.get('id'),
            'message_id': headers.get('message-id'),
            'thread_id': raw.get('threadId'),
            'subject': headers.get('subject') or '',
            'from': self._normalize_address(headers.get('from')),
            'to': self._normalize_addresses(headers.get('to')),
            'cc': self._normalize_addresses(headers.get('cc')),
            'date': self._gmail_date(raw.get('internalDate')),
            'body_html': body_html,
            'body_is_html': body_is_html,
            # Gmail has no hasAttachments flag; a filename on any part is one.
            'has_attachments': any(part.get('filename') for part in self._walk(payload)),
            'headers': self.normalize_headers(headers),
            'is_read': 'UNREAD' not in label_ids,
        }

    @api.model
    def _headers_dict(self, part):
        """All header names lowercased, as the contract requires."""
        return {h['name'].lower(): h['value']
                for h in (part.get('headers') or []) if h.get('name')}

    @api.model
    def _normalize_address(self, value):
        name, email = parseaddr(value or '')
        return {'email': email, 'name': name}

    @api.model
    def _normalize_addresses(self, value):
        return [{'email': email, 'name': name}
                for name, email in getaddresses([value or '']) if email]

    @api.model
    def _walk(self, part):
        """Depth-first over a MIME tree; Gmail nests parts within parts."""
        yield part
        for sub_part in (part.get('parts') or []):
            yield from self._walk(sub_part)

    @api.model
    def _extract_body(self, payload):
        """Prefer text/html, fall back to text/plain. Returns (content, is_html)."""
        html = plain = None
        for part in self._walk(payload):
            if part.get('filename'):
                continue  # an attachment, not the body
            data = (part.get('body') or {}).get('data')
            if not data:
                continue
            mime = part.get('mimeType') or ''
            if mime == 'text/html' and html is None:
                html = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
            elif mime == 'text/plain' and plain is None:
                plain = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
        if html is not None:
            return html, True
        return (plain or ''), False

    @api.model
    def _gmail_date(self, internal_date):
        """Gmail internalDate is epoch milliseconds -> naive UTC, as the cursor wants."""
        if not internal_date:
            return None
        return datetime.fromtimestamp(
            int(internal_date) / 1000, tz=timezone.utc).replace(tzinfo=None)

    # -------------------------------------------------------------------------
    # Identity
    # -------------------------------------------------------------------------
    @api.model
    def test_connection(self, account):
        """Verify the stored credentials still work (see contract)."""
        try:
            token = self.get_valid_token(account)
        except UserError as e:
            return {'success': False, 'error': str(e)}
        try:
            response = requests.get(
                'https://gmail.googleapis.com/gmail/v1/users/me/profile',
                headers={'Authorization': f'Bearer {token}'},
                timeout=10,
            )
            response.raise_for_status()
            profile = response.json()
        except requests.exceptions.RequestException as e:
            return {'success': False, 'error': self._error_detail(e)}
        email = profile.get('emailAddress')
        return {
            'success': True,
            'error': None,
            'email': email,
            # Gmail's profile carries no display name; the address is the identity.
            'display_name': email,
            'id': email,
        }

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

    # -------------------------------------------------------------------------
    # Mailbox actions — contract implementation
    #
    # Gmail's folders are labels, which changes three things and nothing else:
    # a message is in several "folders" at once, a move is a relabel rather
    # than a copy, and its id therefore survives one. Everything the contract
    # promises still holds; delete still files in Trash and still refuses to
    # empty it.
    # -------------------------------------------------------------------------

    def _api_call(self, account, method, url, json=None, params=None):
        """One authenticated Gmail call that writes. `_api_get` is the read half."""
        token = self.get_valid_token(account)
        try:
            response = requests.request(
                method, url,
                headers={'Authorization': f'Bearer {token}',
                         'Content-Type': 'application/json'},
                json=json, params=params or {}, timeout=30,
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            detail = self._error_detail(e)
            _logger.error('[Gmail API] %s %s failed: %s', method.upper(), url, detail)
            raise UserError(_('Gmail refused the request: %s') % detail)
        if not (response.content or b'').strip():
            return {}
        try:
            return response.json()
        except ValueError:
            return {}

    @api.model
    def _as_id_list(self, provider_message_ids):
        """One id or many, always a list."""
        if not provider_message_ids:
            return []
        if isinstance(provider_message_ids, str):
            return [provider_message_ids]
        return [i for i in provider_message_ids if i]

    # ---- folders ------------------------------------------------------------

    @api.model
    def list_folders(self, account, mailbox):
        """Every label in the mailbox, with the role each one claims.

        Gmail nests labels by putting "/" in the name, so the hierarchy needs
        no walking: one call returns the lot, parents and children alike.
        """
        data = self._api_get(
            account, 'https://gmail.googleapis.com/gmail/v1/users/me/labels')
        folders = []
        for raw in data.get('labels') or []:
            label_id = raw.get('id')
            if not label_id:
                continue
            folders.append({
                'id': label_id,
                'name': raw.get('name') or label_id,
                'role': self._LABEL_ROLES.get(label_id),
            })
        return folders

    @api.model
    def _refuse_if_load_bearing(self, folder, verb):
        """Refuse a system label. Gmail marks them `type: system`, but the ones
        that matter are exactly the roles this contract names, and Gmail's own
        API refuses the write anyway — with a 400 that names nothing a person
        can act on."""
        label_id = self._LABEL_MAP.get(folder, folder)
        role = self._LABEL_ROLES.get(label_id)
        if role:
            raise UserError(_(
                'The %(role)s label cannot be %(verb)s: mail is filed there without '
                'anyone asking, including by Gmail itself.',
                role=role, verb=verb,
            ))

    @api.model
    def create_folder(self, account, mailbox, name, parent=None):
        """Create a label, optionally under `parent` (see contract).

        Gmail's hierarchy delimiter is "/" and its labels are named by path, so
        a child is its parent's name plus the child's — which is exactly why
        the caller passes a parent rather than typing the path: the parent is
        addressed by id here and its current name looked up, so a label that
        was renamed yesterday still takes children today.
        """
        name = (name or '').strip()
        if not name:
            raise UserError(_('A folder needs a name.'))
        existing = self.list_folders(account, mailbox)
        if parent:
            parent_name = next(
                (f['name'] for f in existing
                 if f['id'] == self._gmail_label_id(parent) or f['name'] == parent),
                None)
            if not parent_name:
                raise UserError(_('No folder named "%s" to create it inside.') % parent)
            if not name.startswith(parent_name + '/'):
                name = f'{parent_name}/{name}'
        for folder in existing:
            if folder['name'].lower() == name.lower():
                # Asked for, and already true. Not a failure.
                return folder['id'], False
        created = self._api_call(
            account, 'post', 'https://gmail.googleapis.com/gmail/v1/users/me/labels',
            json={'name': name, 'labelListVisibility': 'labelShow',
                  'messageListVisibility': 'show'},
        )
        return created.get('id'), True

    @api.model
    def rename_folder(self, account, mailbox, folder, new_name):
        """Rename a label, keeping it where it is in the hierarchy."""
        new_name = (new_name or '').strip()
        if not new_name:
            raise UserError(_('A folder needs a name.'))
        self._refuse_if_load_bearing(folder, _('renamed'))
        label_id = self._gmail_label_id(folder)
        current = next((f for f in self.list_folders(account, mailbox)
                        if f['id'] == label_id), None)
        if not current:
            raise UserError(_('No folder named "%s".') % folder)
        # Keep it where it is: a bare name would move a nested label to the top.
        if '/' in current['name'] and '/' not in new_name:
            new_name = current['name'].rsplit('/', 1)[0] + '/' + new_name
        self._api_call(
            account, 'patch',
            f'https://gmail.googleapis.com/gmail/v1/users/me/labels/{label_id}',
            json={'name': new_name},
        )
        return label_id

    @api.model
    def delete_folder(self, account, mailbox, folder):
        """Delete an EMPTY label (see contract).

        Deleting a Gmail label does not delete its mail — Gmail only takes the
        label off. The emptiness check is kept anyway, and deliberately: a
        caller writing against this contract has to be able to rely on
        "delete_folder never loses mail" without knowing which provider is
        underneath, and the safe rule is the one IMAP needs.
        """
        self._refuse_if_load_bearing(folder, _('deleted'))
        label_id = self._gmail_label_id(folder)
        folders = self.list_folders(account, mailbox)
        current = next((f for f in folders if f['id'] == label_id), None)
        if not current:
            raise UserError(_('No folder named "%s".') % folder)
        children = sorted(f['name'] for f in folders
                          if f['name'].startswith(current['name'] + '/'))
        if children:
            raise UserError(_(
                '"%(name)s" still has folders inside it (%(children)s). Delete those '
                'first, innermost one first.',
                name=current['name'], children=', '.join(children[:5]),
            ))
        held = self._api_get(
            account, 'https://gmail.googleapis.com/gmail/v1/users/me/messages',
            {'labelIds': label_id, 'maxResults': 1},
        )
        if held.get('messages'):
            raise UserError(_(
                '"%s" still holds mail. Empty it first: delete the messages (they go '
                'to Trash and can be fished back out) or move them somewhere else.'
            ) % current['name'])
        self._api_call(
            account, 'delete',
            f'https://gmail.googleapis.com/gmail/v1/users/me/labels/{label_id}')
        return label_id

    # ---- searching ----------------------------------------------------------

    @api.model
    def search_messages(self, account, mailbox, folder=FOLDER_INBOX, query=None,
                        sender=None, unread_only=False, flagged_only=False,
                        has_attachment=False, limit=50):
        """Search one label, newest first (see contract).

        Gmail's own query language takes every term this method has, so nothing
        is narrowed in Python here — the search operators ARE the compile
        target. `list` answers newest first and offers no ordering control,
        which for once is the order wanted.
        """
        limit = max(1, min(int(limit or 50), 200))
        terms = ['-in:chats']
        if query:
            terms.append(str(query))
        if sender:
            terms.append('from:%s' % sender)
        if unread_only:
            terms.append('is:unread')
        if flagged_only:
            terms.append('is:starred')
        if has_attachment:
            terms.append('has:attachment')

        params = {'maxResults': limit, 'q': ' '.join(terms)}
        if folder == FOLDER_ARCHIVE:
            # No label to ask for: archived is what is left once the three
            # location labels are off.
            params['q'] += ' -in:inbox -in:spam -in:trash'
        else:
            params['labelIds'] = self._gmail_label_id(folder)

        data = self._api_get(
            account, 'https://gmail.googleapis.com/gmail/v1/users/me/messages', params)
        messages = []
        for entry in (data.get('messages') or [])[:limit]:
            raw = self._gmail_get_message(
                account, entry['id'], fmt='metadata',
                headers=['Message-Id', 'Subject', 'From', 'To', 'Cc'])
            messages.append(self._normalize_message(raw))
        return messages

    # ---- message state ------------------------------------------------------

    @api.model
    def set_seen(self, account, mailbox, provider_message_ids, seen=True):
        """Mark messages read or unread (see contract). Gmail's UNREAD label is
        the marker, so "read" is its absence."""
        key = 'removeLabelIds' if seen else 'addLabelIds'
        return self._modify_labels(account, provider_message_ids, {key: ['UNREAD']})

    @api.model
    def set_flagged(self, account, mailbox, provider_message_ids, flagged=True):
        """Star messages or unstar them (see contract)."""
        key = 'addLabelIds' if flagged else 'removeLabelIds'
        return self._modify_labels(account, provider_message_ids, {key: ['STARRED']})

    def _modify_labels(self, account, provider_message_ids, payload):
        """Apply one label change to each message. Returns how many were changed."""
        count = 0
        for message_id in self._as_id_list(provider_message_ids):
            self._api_call(
                account, 'post',
                f'https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}/modify',
                json=payload,
            )
            count += 1
        return count

    # ---- filing -------------------------------------------------------------

    @api.model
    def move_messages(self, account, mailbox, provider_message_ids, destination):
        """Move messages into `destination` (see contract).

        A relabel, not a copy: take off whichever location label the message
        carries, put on the target. So the ids come back unchanged, which is
        the honest answer here — Gmail keeps them across a move, and pretending
        otherwise would send a caller looking for a message that never moved.

        FOLDER_ARCHIVE is the case with no label to put on: archived is a
        message with none of the three, which is why it is named here rather
        than resolved like the others.
        """
        message_ids = self._as_id_list(provider_message_ids)
        payload = {'removeLabelIds': list(self._LOCATION_LABELS)}
        if destination != FOLDER_ARCHIVE:
            target = self._gmail_label_id(destination)
            payload['addLabelIds'] = [target]
            payload['removeLabelIds'] = [label for label in self._LOCATION_LABELS
                                         if label != target]
        self._modify_labels(account, message_ids, payload)
        return message_ids

    @api.model
    def delete_messages(self, account, mailbox, provider_message_ids):
        """Move messages to Trash (see contract).

        `trash`, never `delete`: Gmail's DELETE is permanent and skips the
        Trash entirely, which is the one thing this method exists not to do.
        """
        message_ids = self._as_id_list(provider_message_ids)
        for message_id in message_ids:
            raw = self._gmail_get_message(account, message_id, fmt='minimal')
            if 'TRASH' in (raw.get('labelIds') or []):
                raise UserError(_(
                    'Those messages are already in Trash. Emptying it is not something '
                    'Mail Pro does for you: move them somewhere else, or delete them '
                    'in Gmail.'
                ))
            self._api_call(
                account, 'post',
                f'https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}/trash',
            )
        return message_ids, 'TRASH'

    # ---- drafts -------------------------------------------------------------

    @api.model
    def save_draft(self, mail_record, mailbox, account, reply_context=None):
        """Store `mail_record` in Drafts without sending (see contract).

        The same MIME `send_message` builds, so what is reviewed is what
        leaves. Gmail addresses drafts by a draft id of their own rather than
        by the message id inside them, and that draft id is what comes back:
        it is what `update_draft` and `send_draft` take, and it is opaque to
        the caller either way.
        """
        payload, error = self._draft_payload(mail_record, mailbox, reply_context)
        if error:
            raise UserError(_('Could not save the draft: %s') % error.get('error'))
        created = self._api_call(
            account, 'post', 'https://gmail.googleapis.com/gmail/v1/users/me/drafts',
            json=payload,
        )
        return created.get('id')

    @api.model
    def update_draft(self, mail_record, mailbox, account, provider_message_id,
                     reply_context=None):
        """Replace a stored draft's content (see contract).

        Gmail replaces the whole message behind the draft, so the threading has
        to be rebuilt rather than inherited. When the caller says nothing, the
        draft's own In-Reply-To and References are read back off it first: an
        edit rewrites the message, and a reviewed reply that quietly became a
        new conversation is the failure this prevents.
        """
        if reply_context is None:
            reply_context = self._draft_reply_context(account, provider_message_id)
        payload, error = self._draft_payload(mail_record, mailbox, reply_context)
        if error:
            raise UserError(_('Could not update the draft: %s') % error.get('error'))
        updated = self._api_call(
            account, 'put',
            f'https://gmail.googleapis.com/gmail/v1/users/me/drafts/{provider_message_id}',
            json=payload,
        )
        return updated.get('id') or provider_message_id

    @api.model
    def send_draft(self, account, mailbox, provider_message_id):
        """Send a stored draft as it stands (see contract).

        Gmail sends the stored bytes and removes the draft itself, so what was
        approved is what leaves and there is no cleanup to fail at.
        """
        raw = self._api_get(
            account,
            f'https://gmail.googleapis.com/gmail/v1/users/me/drafts/{provider_message_id}',
            {'format': 'metadata', 'metadataHeaders': ['Message-Id']},
        )
        headers = self._headers_dict((raw.get('message') or {}).get('payload') or {})
        sent = self._api_call(
            account, 'post',
            'https://gmail.googleapis.com/gmail/v1/users/me/drafts/send',
            json={'id': provider_message_id},
        )
        return {
            'success': True,
            'error': None,
            'error_code': None,
            'message_id': headers.get('message-id'),
            'thread_id': sent.get('threadId'),
        }

    @api.model
    def _draft_payload(self, mail_record, mailbox, reply_context=None):
        """Build the `{'message': {...}}` body a draft write takes.

        Returns:
            tuple: (payload, error) — `error` is a failed send result, and is
            None when there is nothing wrong.
        """
        reply_context = reply_context or {}
        to_addrs = mime_utils.collect_recipients(mail_record.email_to,
                                                 mail_record.recipient_ids)
        cc_addrs = mime_utils.collect_recipients(mail_record.email_cc)
        if not to_addrs and not cc_addrs:
            return None, {
                'success': False,
                'error': 'No recipients specified (no email_to, recipient_ids, or email_cc with emails)',
                'error_code': ERROR_NO_RECIPIENTS,
            }
        message_id = mime_utils.new_message_id(mailbox.email)
        msg = mime_utils.build_message(
            mail_record, mailbox.email, to_addrs, cc_addrs, message_id,
            reply_context=reply_context)
        message = {'raw': base64.urlsafe_b64encode(msg.as_bytes()).decode()}
        if reply_context.get('thread_id') and reply_context.get('in_reply_to'):
            message['threadId'] = reply_context['thread_id']
        return {'message': message}, None

    @api.model
    def _draft_reply_context(self, account, draft_id):
        """The threading of a stored draft, for an edit that did not mention any."""
        try:
            raw = self._api_get(
                account,
                f'https://gmail.googleapis.com/gmail/v1/users/me/drafts/{draft_id}',
                {'format': 'metadata',
                 'metadataHeaders': ['In-Reply-To', 'References']},
            )
        except UserError:
            # A draft we cannot read the headers off still gets rewritten; it
            # just loses its place in the thread, which beats refusing the edit.
            _logger.warning('[Gmail API] Could not read threading off draft %s', draft_id)
            return {}
        message = raw.get('message') or {}
        headers = self._headers_dict(message.get('payload') or {})
        return {
            'in_reply_to': headers.get('in-reply-to'),
            'references': (headers.get('references') or '').split(),
            'thread_id': message.get('threadId'),
            'provider_message_id': None,
        }
