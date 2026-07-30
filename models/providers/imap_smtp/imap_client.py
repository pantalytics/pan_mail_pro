# -*- coding: utf-8 -*-
"""IMAP + SMTP client — the third implementation of `mail.provider.client`.

Where Microsoft and Google give us an HTTP API with a vendor's opinions baked
in, this one talks the protocols themselves (imaplib / smtplib, both stdlib —
no new dependency). It is what a mailbox at a plain hoster such as Soverin
needs, and what any server speaking IMAP4rev1 + SMTP will accept.

The protocols answer three of the contract's questions differently, and those
three differences are the whole file:

- **There is no OAuth.** Credentials are a host, a login and a password, stored
  Fernet-encrypted on `pan.mail.account` next to everyone else's tokens. The
  token-lifecycle methods exist because the contract has them, and they refuse
  clearly rather than pretend.

- **There is no message id that survives.** IMAP addresses messages by UID,
  which is only meaningful inside one folder and only while the folder's
  UIDVALIDITY is unchanged. So `provider_message_id` is the triple
  ``folder:uidvalidity:uid`` — everything needed to find the message again, and
  self-invalidating when the server renumbers.

- **There is no thread id.** Graph has conversationId, Gmail has threadId; MIME
  has the References chain. Its root stands in as the thread key, which gives
  every message in a conversation the same handle — the property the caller
  actually needs.

One thing the APIs do for free and SMTP does not: putting the sent message in
the Sent folder. We APPEND it ourselves, best-effort, so a user's own mail
client shows the mail Odoo sent. The X-Odoo-* loop guard on it keeps the
incoming sync from importing it straight back.

Known cost: each contract call opens its own connection, so a message the
processor decides to keep costs a login for the body and another for its
attachments. Steady state is a handful of connections per cron run, which is
why no pool is kept here; a large first sync is slow rather than broken,
because ir.cron will not run the job concurrently with itself.
"""
import imaplib
import logging
import re
import smtplib
import ssl
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email import message_from_bytes, policy
from email.utils import getaddresses, parsedate_to_datetime, parseaddr

from odoo import models, api, _
from odoo.exceptions import UserError
from ...mail_provider_client import ERROR_NO_RECIPIENTS, FOLDER_INBOX, FOLDER_SENT
from .. import mime_utils

_logger = logging.getLogger(__name__)

# Network timeouts. Generous enough for a slow hoster, short enough that a dead
# server cannot hold the 1-minute incoming cron open forever.
IMAP_TIMEOUT = 30
SMTP_TIMEOUT = 30

# INTERNALDATE looks like "12-May-2026 10:00:00 +0200". Parsed by hand rather
# than with %b, which is locale-dependent and would break on a non-English host.
_INTERNALDATE_RE = re.compile(
    r'INTERNALDATE "(\d{1,2})-(\w{3})-(\d{4}) (\d{2}):(\d{2}):(\d{2}) ([+-]\d{4})"')
_MONTHS = {m: i for i, m in enumerate(
    ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], start=1)}

_UID_RE = re.compile(r'\bUID (\d+)')
_FLAGS_RE = re.compile(r'FLAGS \(([^)]*)\)')
# Servers advertise their Sent folder with the \Sent special-use flag (RFC 6154).
_SENT_FLAG_RE = re.compile(r'\\Sent', re.IGNORECASE)


class ImapSmtpClient(models.AbstractModel):
    """IMAP/SMTP implementation of the `mail.provider.client` contract."""

    _name = 'imap.smtp.client'
    _inherit = 'mail.provider.client'
    _description = 'IMAP / SMTP Client'

    # No send-as, no delegation: on IMAP an address *is* a login. A shared
    # mailbox is therefore its own account, the way a Gmail one is, and every
    # mailbox type is serviceable as long as somebody enters its credentials.
    supports_shared_mailbox = False
    supports_delegation = False
    supported_mailbox_types = ('personal', 'shared', 'notification')

    @api.model
    def provider_code(self):
        return 'imap'

    @api.model
    def provider_label(self):
        return 'IMAP / SMTP'

    # -------------------------------------------------------------------------
    # Credentials
    # -------------------------------------------------------------------------
    @api.model
    def account_is_connected(self, account):
        """No token to refresh — a server, a login and a password is the whole
        of it. Both hosts are required because sending and receiving are
        separate services here, unlike the API providers where one credential
        covers both."""
        return bool(
            account.imap_host and account.smtp_host
            and (account.username or account.email)
            and account.password_encrypted
        )

    @api.model
    def account_for_user(self, user):
        return self.env['pan.mail.account']._for_user(user, self.provider_code())

    @api.model
    def resolve_sending_account(self, mailbox, author_user=None):
        """The author is irrelevant: SMTP has no send-as, so a mailbox is sent
        from with its own login or not at all."""
        return self.resolve_receiving_account(mailbox)

    @api.model
    def resolve_receiving_account(self, mailbox):
        """Credentials are keyed on the address, whoever owns them.

        An IMAP mailbox has exactly one login, and it is the login *for that
        address*. Whether an Odoo user is attached to it (a personal mailbox) or
        not (a shared one) changes nothing about which credentials apply, so the
        address is looked up first and the owner is only a fallback for the case
        where an admin filed the credentials under the user instead.
        """
        if mailbox.email:
            account = self.env['pan.mail.account'].sudo().with_context(
                active_test=False).search([
                    ('provider', '=', self.provider_code()),
                    ('email', '=ilike', mailbox.email),
                ], limit=1)
            if account:
                return account
        return self.account_for_user(mailbox.x_owner_user_id)

    # -------------------------------------------------------------------------
    # Authentication
    #
    # There is no authorization flow to run and no token to refresh. These
    # refuse in the contract's terms rather than raising AttributeError three
    # frames deeper.
    # -------------------------------------------------------------------------
    @api.model
    def _no_oauth_error(self):
        return UserError(_(
            'IMAP/SMTP accounts are not connected through a consent screen. '
            'Enter the server, login and password on the email account instead '
            '(Settings → Technical → Email → Email Accounts).'
        ))

    @api.model
    def get_authorization_url(self, redirect_uri, state=None):
        raise self._no_oauth_error()

    @api.model
    def exchange_code_for_tokens(self, authorization_code, redirect_uri):
        raise self._no_oauth_error()

    @api.model
    def refresh_access_token(self, account):
        raise self._no_oauth_error()

    @api.model
    def get_valid_token(self, account):
        raise self._no_oauth_error()

    @api.model
    def get_user_email(self, token):
        """No token authenticates anybody here; the address is configuration,
        not something the server tells us."""
        return None

    @api.model
    def test_connection(self, account):
        """Check both halves — a mailbox that can read but not send is broken.

        Returns the contract's dict; never raises, so the account form can show
        the reason instead of a traceback.
        """
        address = account.email
        try:
            with self._imap(account) as conn:
                conn.select('INBOX', readonly=True)
        except Exception as e:
            return {'success': False, 'error': _('IMAP: %s') % self._error_text(e)}
        try:
            with self._smtp(account):
                pass
        except Exception as e:
            return {'success': False, 'error': _('SMTP: %s') % self._error_text(e)}
        return {
            'success': True,
            'error': None,
            'email': address,
            'display_name': address,
            'id': address,
        }

    # -------------------------------------------------------------------------
    # Connections
    # -------------------------------------------------------------------------
    @contextmanager
    def _imap(self, account):
        """An authenticated IMAP connection, closed whatever happens."""
        self._require_credentials(account)
        host, port = account.imap_host, account.imap_port or 993
        try:
            if account.imap_security == 'ssl':
                conn = imaplib.IMAP4_SSL(host, port, timeout=IMAP_TIMEOUT,
                                         ssl_context=ssl.create_default_context())
            else:
                conn = imaplib.IMAP4(host, port, timeout=IMAP_TIMEOUT)
                if account.imap_security == 'starttls':
                    conn.starttls(ssl.create_default_context())
            conn.login(account._imap_login(), account.password)
        except (imaplib.IMAP4.error, OSError) as e:
            raise UserError(_(
                'Could not connect to IMAP server %(host)s: %(error)s',
                host=host, error=self._error_text(e),
            ))
        try:
            yield conn
        finally:
            try:
                conn.logout()
            except Exception:
                # A dropped socket on the way out is not a failure of the work
                # that already succeeded.
                _logger.debug('[IMAP] Ignoring error while closing connection to %s', host)

    @contextmanager
    def _smtp(self, account):
        """An authenticated SMTP connection, closed whatever happens."""
        self._require_credentials(account)
        host, port = account.smtp_host, account.smtp_port or 465
        try:
            if account.smtp_security == 'ssl':
                conn = smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT,
                                        context=ssl.create_default_context())
            else:
                conn = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT)
                if account.smtp_security == 'starttls':
                    conn.starttls(context=ssl.create_default_context())
            conn.login(account._imap_login(), account.password)
        except (smtplib.SMTPException, OSError) as e:
            raise UserError(_(
                'Could not connect to SMTP server %(host)s: %(error)s',
                host=host, error=self._error_text(e),
            ))
        try:
            yield conn
        finally:
            try:
                conn.quit()
            except Exception:
                _logger.debug('[SMTP] Ignoring error while closing connection to %s', host)

    def _require_credentials(self, account):
        if not self.account_is_connected(account):
            raise UserError(_(
                'Email account "%s" is missing its server, login or password.'
            ) % (account.email or account.display_name))

    # -------------------------------------------------------------------------
    # Folders
    # -------------------------------------------------------------------------
    @api.model
    def _folder_name(self, conn, account, folder):
        """Translate a contract folder id into this server's folder name.

        INBOX is the one name IMAP guarantees. Everything else is the server's
        choice — 'Sent', 'Sent Items', 'INBOX.Sent', a localized name — so the
        \\Sent special-use flag is asked for first, an admin override beats it,
        and 'Sent' is the last resort.
        """
        if folder == FOLDER_INBOX:
            return 'INBOX'
        if folder != FOLDER_SENT:
            raise UserError(_('Unknown mail folder: %s') % folder)
        if account.imap_sent_folder:
            return account.imap_sent_folder
        return self._detect_sent_folder(conn) or 'Sent'

    @api.model
    def _detect_sent_folder(self, conn):
        try:
            typ, data = conn.list()
        except (imaplib.IMAP4.error, OSError):
            return None
        if typ != 'OK':
            return None
        for line in data or []:
            text = line.decode(errors='replace') if isinstance(line, bytes) else str(line)
            if _SENT_FLAG_RE.search(text):
                # LIST lines end with the folder name, quoted when it has spaces.
                match = re.search(r'"([^"]*)"\s*$', text) or re.search(r'(\S+)\s*$', text)
                if match:
                    return match.group(1)
        return None

    @api.model
    def _select(self, conn, account, folder):
        """Open a folder read-only and return (name, uidvalidity).

        Read-only on purpose: syncing must never mark someone's mail as read.
        """
        name = self._folder_name(conn, account, folder)
        typ, _data = conn.select(self._quote(name), readonly=True)
        if typ != 'OK':
            raise UserError(_('Could not open IMAP folder "%s".') % name)
        uidvalidity = (conn.response('UIDVALIDITY')[1] or [b''])[0]
        return name, (uidvalidity or b'').decode() or '0'

    @api.model
    def _quote(self, name):
        return '"%s"' % name.replace('"', '\\"')

    # -------------------------------------------------------------------------
    # Message ids
    # -------------------------------------------------------------------------
    @api.model
    def _message_ref(self, folder, uidvalidity, uid):
        return f'{folder}:{uidvalidity}:{uid}'

    @api.model
    def _parse_message_ref(self, provider_message_id):
        try:
            folder, uidvalidity, uid = (provider_message_id or '').split(':')
        except ValueError:
            raise UserError(_('Not an IMAP message reference: %s') % provider_message_id)
        return folder, uidvalidity, uid

    # -------------------------------------------------------------------------
    # Sending
    # -------------------------------------------------------------------------
    @api.model
    def send_message(self, mail_record, mailbox, account):
        """Send one mail.mail over SMTP and file a copy in Sent.

        The Message-ID is ours (we build the MIME), so it is returned as-is —
        the same handle dedup and reply-threading use for the other providers.
        """
        to_addrs = mime_utils.collect_recipients(mail_record.email_to, mail_record.recipient_ids)
        cc_addrs = mime_utils.collect_recipients(mail_record.email_cc)
        if not to_addrs and not cc_addrs:
            return {
                'success': False,
                'error': 'No recipients specified (no email_to, recipient_ids, or email_cc with emails)',
                # Same code the other clients return, so mail.mail.send() cancels
                # this one instead of aborting the batch.
                'error_code': ERROR_NO_RECIPIENTS,
            }

        message_id = mime_utils.new_message_id(mailbox.email)
        msg = mime_utils.build_message(
            mail_record, mailbox.email, to_addrs, cc_addrs, message_id)
        envelope = mime_utils.bare_addresses(to_addrs + cc_addrs)

        try:
            with self._smtp(account) as conn:
                conn.send_message(msg, from_addr=parseaddr(mailbox.email)[1] or mailbox.email,
                                  to_addrs=envelope)
        except UserError as e:
            return {'success': False, 'error': str(e), 'error_code': None}
        except (smtplib.SMTPException, OSError) as e:
            _logger.error('[SMTP] Sending mail %s from %s failed: %s',
                          mail_record.id, mailbox.email, self._error_text(e))
            return {'success': False, 'error': self._error_text(e), 'error_code': None}

        self._append_to_sent(account, msg)

        return {
            'success': True,
            'error': None,
            'error_code': None,
            'message_id': message_id,
            'thread_id': mime_utils.thread_key(msg, message_id),
        }

    @api.model
    def _append_to_sent(self, account, msg):
        """File a copy of a sent message in the Sent folder.

        Graph and Gmail do this themselves; SMTP does not, so a user's own mail
        client would show no record of anything Odoo sent. Best-effort: the mail
        is already delivered, and failing to file a copy must not report the
        send as failed.
        """
        try:
            with self._imap(account) as conn:
                folder = (account.imap_sent_folder
                          or self._detect_sent_folder(conn) or 'Sent')
                conn.append(self._quote(folder), '\\Seen', None, msg.as_bytes())
        except Exception as e:
            _logger.warning('[IMAP] Could not file sent copy for %s: %s',
                            account.email, self._error_text(e))

    # -------------------------------------------------------------------------
    # Receiving
    # -------------------------------------------------------------------------
    @api.model
    def fetch_messages(self, account, mailbox, folder=FOLDER_INBOX,
                       since_datetime=None, limit=50):
        """List messages in a folder, oldest first (see contract).

        Two details of IMAP's SEARCH shape this:

        - SINCE is *date* granular and works on the server's timezone, so it is
          widened by a day and the exact cutoff is applied here. Overlap is
          harmless: the processor dedups on Message-ID before doing any work.
        - The oldest `limit` matches are taken, not the newest. The caller
          advances its cursor to the last message of the batch, so taking the
          newest would step over everything older and never come back for it.
        """
        with self._imap(account) as conn:
            name, uidvalidity = self._select(conn, account, folder)
            uids = self._search(conn, since_datetime)
            if not uids:
                return []
            uids = uids[:limit]

            typ, data = conn.uid(
                'FETCH', b','.join(uids), '(UID FLAGS INTERNALDATE BODY.PEEK[HEADER])')
            if typ != 'OK':
                raise UserError(_('Could not read messages from folder "%s".') % name)

            messages = [
                self._normalize_message(item, folder, uidvalidity)
                for item in self._parse_fetch(data)
            ]

        if since_datetime:
            messages = [m for m in messages
                        if m['date'] is None or m['date'] >= since_datetime]
        messages.sort(key=lambda m: m['date'] or datetime.min)
        return messages

    @api.model
    def _search(self, conn, since_datetime):
        """UIDs matching the cursor, ascending (which is arrival order)."""
        if since_datetime:
            # A day of slack absorbs the server's timezone and SINCE's date
            # granularity; the exact filter is applied by the caller.
            since = (since_datetime - timedelta(days=1)).strftime('%d-%b-%Y')
            typ, data = conn.uid('SEARCH', None, 'SINCE', since)
        else:
            typ, data = conn.uid('SEARCH', None, 'ALL')
        if typ != 'OK':
            raise UserError(_('IMAP search failed.'))
        return (data[0] or b'').split()

    @api.model
    def get_message(self, account, mailbox, provider_message_id):
        """Fetch one message in full, including headers and body."""
        raw = self._fetch_one(account, provider_message_id)
        folder, uidvalidity, _uid = self._parse_message_ref(provider_message_id)
        return self._normalize_message(raw, folder, uidvalidity)

    @api.model
    def get_message_attachments(self, account, mailbox, provider_message_id):
        """Return normalized attachments; never raises (see contract)."""
        attachments = []
        try:
            item = self._fetch_one(account, provider_message_id)
            msg = message_from_bytes(item['raw'], policy=policy.default)
            for part in msg.walk():
                filename = part.get_filename()
                if not filename and not part.get('content-id'):
                    continue
                if part.get_content_maintype() == 'multipart':
                    continue
                content = part.get_payload(decode=True)
                if not content:
                    continue
                content_id = (part.get('content-id') or '').strip('<>')
                disposition = (part.get('content-disposition') or '').lower()
                # Inline if the sender said so or gave it a Content-ID for the
                # body to reference — the same test the other clients apply.
                is_inline = 'inline' in disposition or bool(content_id)
                attachments.append({
                    'name': filename or (content_id or 'attachment'),
                    'mimetype': part.get_content_type() or 'application/octet-stream',
                    'content': content,
                    'is_inline': is_inline,
                    'content_id': content_id or None,
                })
        except Exception as e:
            # Contract: an attachment failure must not sink the message.
            _logger.warning('[IMAP] Could not fetch attachments for %s: %s',
                            provider_message_id, self._error_text(e))
            return []
        return attachments

    @api.model
    def _fetch_one(self, account, provider_message_id):
        """Fetch one full message by its `folder:uidvalidity:uid` reference."""
        folder, uidvalidity, uid = self._parse_message_ref(provider_message_id)
        with self._imap(account) as conn:
            name, current = self._select(conn, account, folder)
            if current != uidvalidity:
                # The server renumbered the folder; every UID we hold for it is
                # meaningless. Saying so beats fetching whatever now sits there.
                raise UserError(_(
                    'Folder "%(folder)s" was renumbered by the server '
                    '(UIDVALIDITY %(old)s -> %(new)s); re-sync it.',
                    folder=name, old=uidvalidity, new=current,
                ))
            typ, data = conn.uid('FETCH', uid, '(UID FLAGS INTERNALDATE BODY.PEEK[])')
            if typ != 'OK':
                raise UserError(_('Could not read message %s.') % provider_message_id)
            items = self._parse_fetch(data)
            if not items:
                raise UserError(_('Message %s is no longer in the mailbox.') % provider_message_id)
            return items[0]

    # -------------------------------------------------------------------------
    # IMAP -> normalized translation
    # -------------------------------------------------------------------------
    @api.model
    def _parse_fetch(self, data):
        """Turn imaplib's FETCH response into {uid, flags, internaldate, raw}.

        imaplib hands back a flat list mixing tuples (metadata line, literal)
        with bare bytes for the closing parenthesis. Only the tuples carry a
        message.
        """
        items = []
        for entry in data or []:
            if not isinstance(entry, tuple) or len(entry) < 2:
                continue
            meta = entry[0].decode(errors='replace') if isinstance(entry[0], bytes) else str(entry[0])
            uid_match = _UID_RE.search(meta)
            if not uid_match:
                continue
            flags_match = _FLAGS_RE.search(meta)
            items.append({
                'uid': uid_match.group(1),
                'flags': (flags_match.group(1) if flags_match else '').split(),
                'internaldate': self._internaldate(meta),
                'raw': entry[1] or b'',
            })
        return items

    @api.model
    def _internaldate(self, meta):
        """INTERNALDATE -> naive UTC, which is what the sync cursor compares to."""
        match = _INTERNALDATE_RE.search(meta)
        if not match:
            return None
        day, month, year, hour, minute, second, offset = match.groups()
        if month.capitalize() not in _MONTHS:
            return None
        sign = 1 if offset[0] == '+' else -1
        delta = timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5])) * sign
        return datetime(int(year), _MONTHS[month.capitalize()], int(day),
                        int(hour), int(minute), int(second)) - delta

    @api.model
    def _normalize_message(self, item, folder, uidvalidity):
        """Map one fetched message onto the normalized shape from the contract.

        Works for both fetch shapes: a header-only fetch yields the same dict
        with an empty body, which the contract explicitly allows for list
        results.
        """
        msg = message_from_bytes(item['raw'], policy=policy.default)
        headers = {name.lower(): str(value) for name, value in msg.items()}
        body_html, body_is_html = self._extract_body(msg)
        message_id = headers.get('message-id')

        return {
            'provider_message_id': self._message_ref(folder, uidvalidity, item['uid']),
            'message_id': message_id,
            'thread_id': mime_utils.thread_key(msg, message_id),
            'subject': headers.get('subject') or '',
            'from': self._normalize_address(headers.get('from')),
            'to': self._normalize_addresses(headers.get('to')),
            'cc': self._normalize_addresses(headers.get('cc')),
            'date': item.get('internaldate') or self._header_date(headers.get('date')),
            'body_html': body_html,
            'body_is_html': body_is_html,
            'has_attachments': any(
                part.get_filename() for part in msg.walk() if part.get_filename()),
            'headers': headers,
            'is_read': '\\Seen' in (item.get('flags') or []),
        }

    @api.model
    def _extract_body(self, msg):
        """Prefer text/html, fall back to text/plain. Returns (content, is_html)."""
        try:
            part = msg.get_body(preferencelist=('html', 'plain'))
        except Exception:
            part = None
        if part is None:
            return '', False
        try:
            content = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b''
            content = payload.decode('utf-8', errors='replace')
        if not content:
            # A header-only fetch has the Content-Type but no body. Calling that
            # "empty HTML" would have the caller wrap '' in Markup for nothing.
            return '', False
        return content, part.get_content_subtype() == 'html'

    @api.model
    def _header_date(self, value):
        """Date: header -> naive UTC. Only used when INTERNALDATE is missing."""
        if not value:
            return None
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if parsed is None:
            return None
        if parsed.tzinfo:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    @api.model
    def _normalize_address(self, value):
        name, email = parseaddr(value or '')
        return {'email': email, 'name': name}

    @api.model
    def _normalize_addresses(self, value):
        return [{'email': email, 'name': name}
                for name, email in getaddresses([value or '']) if email]

    # -------------------------------------------------------------------------
    # Errors
    # -------------------------------------------------------------------------
    @api.model
    def _error_text(self, exc):
        """Readable text for protocol errors, whose args are often raw bytes."""
        if isinstance(exc, UserError):
            return str(exc)
        parts = []
        for arg in getattr(exc, 'args', ()) or ():
            parts.append(arg.decode(errors='replace') if isinstance(arg, bytes) else str(arg))
        return ' '.join(p for p in parts if p) or str(exc) or type(exc).__name__
