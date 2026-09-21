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
from email.utils import (
    format_datetime, getaddresses, parsedate_to_datetime, parseaddr,
)

from odoo import models, api, _
from odoo.exceptions import UserError
from ...mail_provider_client import (
    ERROR_NO_RECIPIENTS, FOLDER_DRAFTS, FOLDER_INBOX, FOLDER_ROLES, FOLDER_SENT,
    FOLDER_TRASH, UNREAD_CAP,
)
from .. import mime_utils

_logger = logging.getLogger(__name__)

# Network timeouts. Generous enough for a slow hoster, short enough that a dead
# server cannot hold the 1-minute incoming cron open forever.
IMAP_TIMEOUT = 30
SMTP_TIMEOUT = 30
# The uplink the payload-scaled SMTP timeout assumes. A flat timeout silently
# demands a fast connection for a large attachment: 25 MB inside 30 s is
# ~7 Mbit/s up, which plenty of office connections do not have.
MIN_UPLOAD_BYTES_PER_SEC = 50 * 1024

# How many UIDs are asked for INTERNALDATE at a time while narrowing a widened
# SEARCH SINCE window down to the real cursor. See `_oldest_uids`.
UID_DATE_PROBE_CHUNK = 500

# INTERNALDATE looks like "12-May-2026 10:00:00 +0200". Parsed by hand rather
# than with %b, which is locale-dependent and would break on a non-English host.
#
# The day is `date-day-fixed` in RFC 3501: two characters, space-padded, so the
# first nine days of every month arrive as `" 1-May-2026 ..."`. Requiring a
# digit there made `_internaldate()` answer None for roughly a third of the
# calendar — silently, because the caller just fell back to the header Date.
# imaplib's own pattern uses `[ 0123][0-9]` for exactly this reason.
_INTERNALDATE_RE = re.compile(
    r'INTERNALDATE "([ \d]?\d)-(\w{3})-(\d{4}) (\d{2}):(\d{2}):(\d{2}) ([+-]\d{4})"')
_MONTHS = {m: i for i, m in enumerate(
    ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], start=1)}

# `(\HasNoChildren \Sent) "/" "INBOX/Sent"` — flags, delimiter, name. The
# delimiter is "/" on one server and "." on the next, which is why a caller is
# asked for a parent folder rather than left to assemble a path.
_LIST_RE = re.compile(r'^\(([^)]*)\)\s+(?:"([^"]*)"|NIL)\s+(.*)$')
# COPYUID comes back on a UID MOVE / UID COPY from any server speaking UIDPLUS
# (RFC 4315): the destination's UIDVALIDITY, the source uids, the new ones.
_COPYUID_RE = re.compile(r'COPYUID (\d+) ([\d,:]+) ([\d,:]+)', re.IGNORECASE)

_UID_RE = re.compile(r'\bUID (\d+)')
_FLAGS_RE = re.compile(r'FLAGS \(([^)]*)\)')
# Servers advertise their Sent folder with the \Sent special-use flag (RFC 6154).
_SENT_FLAG_RE = re.compile(r'\\Sent', re.IGNORECASE)
# ... and the ones that do not still tend to name it Sent, under whatever
# hierarchy delimiter they use: `INBOX.Sent`, `INBOX/Sent`.
_SENT_LEAF_RE = re.compile(r'(?:^|[./])sent$', re.IGNORECASE)


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
    supported_mailbox_types = ('personal', 'shared')
    # There is no consent screen: credentials are a server, a login and a
    # password, typed in once on the account.
    uses_oauth = False

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
        return self.account_for_user(mailbox.owner_user_id)

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
    def _exchange_code_for_tokens(self, authorization_code, redirect_uri):
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
    def _smtp(self, account, payload_size=0):
        """An authenticated SMTP connection, closed whatever happens.

        The timeout scales with `payload_size`: a large attachment on a slow
        uplink is a working send, not a hung one.
        """
        self._require_credentials(account)
        host, port = account.smtp_host, account.smtp_port or 465
        timeout = self._smtp_timeout(payload_size)
        try:
            if account.smtp_security == 'ssl':
                conn = smtplib.SMTP_SSL(host, port, timeout=timeout,
                                        context=ssl.create_default_context())
            else:
                conn = smtplib.SMTP(host, port, timeout=timeout)
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

    @api.model
    def _smtp_timeout(self, payload_size):
        return SMTP_TIMEOUT + payload_size // MIN_UPLOAD_BYTES_PER_SEC

    @api.model
    def _check_size(self, conn, size):
        """Refuse before DATA what the server's SIZE extension (RFC 1870) says
        it will refuse after — the same rejection, minus the wasted upload and
        with a reason the sender can act on. A server advertising no limit is
        left alone.
        """
        raw_limit = (getattr(conn, 'esmtp_features', None) or {}).get('size', '')
        try:
            limit = int(str(raw_limit).split()[0]) if raw_limit else 0
        except (ValueError, IndexError):
            return
        if limit and size > limit:
            raise UserError(_(
                'This email is %(size).1f MB; the SMTP server accepts at most '
                '%(limit).1f MB. Remove or shrink an attachment.',
                size=size / (1024 * 1024), limit=limit / (1024 * 1024),
            ))

    def _require_credentials(self, account):
        self._refuse_when_neutralized()
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
        if not folder:
            raise UserError(_('No mail folder given.'))
        if folder == FOLDER_INBOX:
            return 'INBOX'
        if folder == FOLDER_SENT:
            if account.imap_sent_folder:
                return account.imap_sent_folder
            return self._detect_sent_folder(conn) or 'Sent'
        if folder in FOLDER_ROLES:
            return self._detect_special_folder(conn, folder) or FOLDER_ROLES[folder]
        # Not a role, so it is already this server's own name for a folder —
        # one `list_folders()` handed back. There is nothing to translate.
        return folder

    @api.model
    def _detect_sent_folder(self, conn):
        """Which folder this server files sent mail in, or None.

        Two passes over one LIST. The \\Sent special-use flag is the reliable
        answer and wins. When the server does not advertise it -- Courier and
        some Dovecot namespace setups do not -- a folder *named* Sent under any
        hierarchy delimiter is the answer: `INBOX.Sent` and `INBOX/Sent` are
        the same folder the literal fallback `Sent` was reaching for, and when
        the fallback misses, the APPEND fails and the user's own mail client
        shows no record of anything Odoo sent.

        Still no folder creation. Making folders in somebody's mailbox on a
        guess is worse than not filing the copy.
        """
        try:
            typ, data = conn.list()
        except (imaplib.IMAP4.error, OSError):
            return None
        if typ != 'OK':
            return None
        named_sent = None
        for line in data or []:
            text = line.decode(errors='replace') if isinstance(line, bytes) else str(line)
            name = self._list_line_folder(text)
            if not name:
                continue
            if _SENT_FLAG_RE.search(text):
                return name
            if named_sent is None and _SENT_LEAF_RE.search(name):
                named_sent = name
        return named_sent

    @api.model
    def _list_line_folder(self, text):
        """The folder name at the end of a LIST line, quoted when it has spaces."""
        match = re.search(r'"([^"]*)"\s*$', text) or re.search(r'(\S+)\s*$', text)
        return match.group(1) if match else None

    @api.model
    def _select(self, conn, account, folder, readonly=True):
        """Open a folder and return (name, uidvalidity).

        Read-only by default, on purpose: syncing must never mark someone's
        mail as read. The actions that write say so.
        """
        name = self._folder_name(conn, account, folder)
        typ, _data = conn.select(self._quote(name), readonly=readonly)
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
    def send_message(self, mail_record, mailbox, account, reply_context=None):
        """Send one mail.mail over SMTP and file a copy in Sent.

        The Message-ID is ours (we build the MIME), so it is returned as-is —
        the same handle dedup and reply-threading use for the other providers.

        `reply_context` carries the In-Reply-To / References pair, which is the
        *only* threading signal this provider has: there is no conversation id
        to fall back on, so the thread key returned below is derived from the
        chain these headers establish.
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
            mail_record, mailbox.email, to_addrs, cc_addrs, message_id,
            reply_context=reply_context)
        envelope = mime_utils.bare_addresses(to_addrs + cc_addrs)
        payload_size = len(msg.as_bytes())

        try:
            with self._smtp(account, payload_size=payload_size) as conn:
                self._check_size(conn, payload_size)
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

        A host that files its own copy gets one, not two: the folder is probed
        for the Message-ID first and only an absent copy is APPENDed. A failed
        probe counts as absent — a duplicate in Sent beats no copy at all.
        """
        try:
            with self._imap(account) as conn:
                folder = (account.imap_sent_folder
                          or self._detect_sent_folder(conn) or 'Sent')
                if self._sent_copy_exists(conn, folder, msg['Message-ID']):
                    return
                conn.append(self._quote(folder), '\\Seen', None, msg.as_bytes())
        except Exception as e:
            _logger.warning('[IMAP] Could not file sent copy for %s: %s',
                            account.email, self._error_text(e))

    @api.model
    def _sent_copy_exists(self, conn, folder, message_id):
        if not message_id:
            return False
        try:
            typ, _data = conn.select(self._quote(folder), readonly=True)
            if typ != 'OK':
                return False
            typ, data = conn.uid('SEARCH', None, 'HEADER', 'Message-ID',
                                 self._quote(message_id))
            return typ == 'OK' and bool((data[0] or b'').strip())
        except (imaplib.IMAP4.error, OSError):
            return False

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
            uids = self._oldest_uids(conn, uids, since_datetime, limit)
            if not uids:
                return []

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
    def _oldest_uids(self, conn, uids, since_datetime, limit):
        """The oldest `limit` UIDs that are genuinely at or after the cursor.

        Cutting to `limit` before applying the exact cutoff is the bug this
        exists to prevent. `_search` deliberately asks a day wide, because
        SEARCH SINCE is date-granular and evaluated in the server's timezone.
        On a mailbox that receives more than `limit` messages a day, the first
        `limit` UIDs of that widened window are *all* from the slack day, every
        one of them fails the exact `>= since_datetime` test in the caller, and
        the fetch comes back empty. An empty fetch reads as "caught up", so the
        cursor jumps to now() and the mail in between is gone.

        INTERNALDATE is asked for first, in chunks, and only as far as needed to
        collect `limit` survivors — a metadata-only FETCH is one cheap round
        trip, and stopping early keeps a deep backlog from pulling the whole
        folder's dates over the wire.
        """
        if not since_datetime:
            return uids[:limit]

        kept = []
        for start in range(0, len(uids), UID_DATE_PROBE_CHUNK):
            chunk = uids[start:start + UID_DATE_PROBE_CHUNK]
            dates = self._dates_for(conn, chunk)
            for uid in chunk:
                date = dates.get(uid)
                # No INTERNALDATE means we cannot judge it; keep it and let the
                # header date decide, exactly as the caller's filter does.
                if date is None or date >= since_datetime:
                    kept.append(uid)
            if len(kept) >= limit:
                break
        return kept[:limit]

    @api.model
    def _dates_for(self, conn, uids):
        """{uid: INTERNALDATE as naive UTC} for a UID set, in one round trip.

        A metadata-only FETCH comes back as bare byte lines rather than the
        (metadata, literal) tuples `_parse_fetch` expects, so it is read here.
        """
        typ, data = conn.uid('FETCH', b','.join(uids), '(UID INTERNALDATE)')
        if typ != 'OK':
            raise UserError(_('Could not read message dates from the folder.'))
        dates = {}
        for entry in data or []:
            line = entry[0] if isinstance(entry, tuple) else entry
            if not line:
                continue
            meta = line.decode(errors='replace') if isinstance(line, bytes) else str(line)
            match = _UID_RE.search(meta)
            if match:
                dates[match.group(1).encode()] = self._internaldate(meta)
        return dates

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
        body_html, body_is_html = self.normalize_body(body_html, body_is_html)
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
            'headers': self.normalize_headers(headers),
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

    # -------------------------------------------------------------------------
    # Mailbox actions — contract implementation
    #
    # Three IMAP facts shape everything below:
    #
    # - A folder's NAME is not knowable, only its role. The SPECIAL-USE
    #   attribute (RFC 6154) in the LIST reply is what says which folder is the
    #   Trash, because "Prullenbak" and "Deleted Messages" are the same folder
    #   to everyone but a client guessing at a name.
    # - A UID is folder-scoped, so a move changes a message's whole reference.
    #   The new ones come back; the old ones point at nothing.
    # - EXPUNGE is not scoped to anything. A bare EXPUNGE permanently removes
    #   whatever another mail client left marked \Deleted in the folder, which
    #   is why nothing here issues one: marking uses a raw UID STORE, and the
    #   two places that must really remove a message use UID EXPUNGE (RFC 4315)
    #   on their own uids or refuse.
    # -------------------------------------------------------------------------

    # SPECIAL-USE attributes (RFC 6154) -> this contract's folder roles.
    _ROLE_ATTRIBUTES = {
        '\\sent': FOLDER_SENT,
        '\\trash': FOLDER_TRASH,
        '\\drafts': FOLDER_DRAFTS,
        '\\archive': 'archive',
        '\\junk': 'junk',
    }

    # ---- folders ------------------------------------------------------------

    @api.model
    def _list_folders_raw(self, conn):
        """Every folder on the server as {name, flags, delimiter}."""
        try:
            typ, data = conn.list()
        except (imaplib.IMAP4.error, OSError) as e:
            raise UserError(_('Could not list IMAP folders: %s') % self._error_text(e))
        if typ != 'OK':
            raise UserError(_('Could not list IMAP folders.'))
        folders = []
        for line in data or []:
            text = line.decode(errors='replace') if isinstance(line, bytes) else str(line)
            match = _LIST_RE.match(text.strip())
            if not match:
                continue
            flags, delimiter, raw_name = match.groups()
            name = raw_name.strip()
            if name.startswith('"') and name.endswith('"'):
                name = name[1:-1]
            if not name:
                continue
            folders.append({
                'name': name,
                'flags': flags.split(),
                'delimiter': delimiter or '/',
            })
        return folders

    @api.model
    def _folder_role_from_flags(self, flags):
        """Which contract role a folder's LIST flags claim, if any."""
        lowered = {str(flag).lower() for flag in flags or []}
        for attribute, role in self._ROLE_ATTRIBUTES.items():
            if attribute in lowered:
                return role
        return None

    @api.model
    def _detect_special_folder(self, conn, role):
        """This server's folder for `role`, or None.

        Two passes over one LIST, the same shape `_detect_sent_folder` has
        used for the Sent copy all along: the SPECIAL-USE attribute wins, and a
        folder merely *named* like the role is the fallback for the servers
        that advertise nothing — `INBOX.Trash` and `INBOX/Trash` are the folder
        a bare "Trash" was reaching for.
        """
        fallback_name = FOLDER_ROLES.get(role, '')
        named = None
        for folder in self._list_folders_raw(conn):
            if self._folder_role_from_flags(folder['flags']) == role:
                return folder['name']
            leaf = folder['name'].rsplit(folder['delimiter'], 1)[-1]
            if named is None and leaf.lower() == fallback_name.lower():
                named = folder['name']
        return named

    @api.model
    def list_folders(self, account, mailbox):
        """Every folder in the mailbox, with the role each one claims."""
        with self._imap(account) as conn:
            folders = self._list_folders_raw(conn)
        result = []
        for folder in folders:
            role = self._folder_role_from_flags(folder['flags'])
            if not role and folder['name'].upper() == 'INBOX':
                role = FOLDER_INBOX
            result.append({
                'id': folder['name'],
                'name': folder['name'],
                'role': role,
            })
        return result

    @api.model
    def _refuse_if_load_bearing(self, conn, account, name, verb):
        """INBOX and the role folders are not ours to rename or delete.

        Every one of them is somewhere mail is filed without anybody asking:
        the Sent copy this client APPENDs, the Trash a delete lands in, the
        Drafts a review sits in. The role is read from the folder's own flags
        first and from `_folder_name` second, because a server that advertises
        nothing still has the Trash this module files deletes into — protecting
        only the flagged ones would leave exactly those mailboxes unprotected.
        """
        if name.strip().upper() == 'INBOX':
            raise UserError(_(
                'INBOX cannot be %s: it is the mailbox itself, not a folder in it.'
            ) % verb)
        for folder in self._list_folders_raw(conn):
            if folder['name'] != name:
                continue
            role = self._folder_role_from_flags(folder['flags'])
            if role:
                raise UserError(_(
                    '"%(name)s" is this mailbox\'s %(role)s folder, so it cannot be '
                    '%(verb)s: mail is filed there without anyone asking.',
                    name=name, role=role, verb=verb,
                ))
        for role in FOLDER_ROLES:
            if role != FOLDER_INBOX and self._folder_name(conn, account, role) == name:
                raise UserError(_(
                    '"%(name)s" is this mailbox\'s %(role)s folder, so it cannot be '
                    '%(verb)s: mail is filed there without anyone asking.',
                    name=name, role=role, verb=verb,
                ))

    @api.model
    def create_folder(self, account, mailbox, name, parent=None):
        """Create a folder, optionally inside `parent` (see contract)."""
        name = (name or '').strip()
        if not name:
            raise UserError(_('A folder needs a name.'))
        with self._imap(account) as conn:
            folders = self._list_folders_raw(conn)
            by_name = {f['name']: f for f in folders}
            delimiter = next((f['delimiter'] for f in folders if f['delimiter']), '/')
            full = name
            if parent:
                parent_name = self._folder_name(conn, account, parent)
                if parent_name not in by_name:
                    raise UserError(_('No folder named "%s" to create it inside.') % parent)
                if not full.startswith(parent_name + delimiter):
                    full = parent_name + delimiter + full
            if full in by_name:
                # Asked for, and already true. Not a failure.
                return full, False
            typ, data = conn.create(self._quote(full))
            if typ != 'OK':
                raise UserError(_(
                    'The server refused to create "%(name)s": %(error)s. Some mailboxes '
                    'keep every folder under INBOX — try again with INBOX as the parent.',
                    name=full, error=self._response_text(data),
                ))
            # Not every server subscribes a folder it created, and an
            # unsubscribed folder is invisible in most mail clients.
            try:
                conn.subscribe(self._quote(full))
            except (imaplib.IMAP4.error, OSError):
                _logger.info('[IMAP] Created %s but could not subscribe it', full)
            return full, True

    @api.model
    def rename_folder(self, account, mailbox, folder, new_name):
        """Rename a folder, keeping it where it is (see contract)."""
        new_name = (new_name or '').strip()
        if not new_name:
            raise UserError(_('A folder needs a name.'))
        with self._imap(account) as conn:
            name = self._folder_name(conn, account, folder)
            self._refuse_if_load_bearing(conn, account, name, _('renamed'))
            folders = self._list_folders_raw(conn)
            delimiter = next((f['delimiter'] for f in folders if f['delimiter']), '/')
            # Keep it where it is: a bare name would move a nested folder to
            # the top of the hierarchy, which is a move, not a rename.
            if delimiter in name and delimiter not in new_name:
                new_name = name.rsplit(delimiter, 1)[0] + delimiter + new_name
            typ, data = conn.rename(self._quote(name), self._quote(new_name))
            if typ != 'OK':
                raise UserError(_(
                    'The server refused to rename "%(name)s": %(error)s',
                    name=name, error=self._response_text(data),
                ))
            return new_name

    @api.model
    def delete_folder(self, account, mailbox, folder):
        """Delete an EMPTY folder (see contract).

        IMAP's DELETE takes the folder's messages with it and no Trash catches
        them — the one genuinely irreversible thing this client can do, and
        exactly what `delete_messages` was written not to be. So the mail has
        to be gone first, by hand, and it can go the recoverable way.
        """
        with self._imap(account) as conn:
            name = self._folder_name(conn, account, folder)
            folders = self._list_folders_raw(conn)
            by_name = {f['name']: f for f in folders}
            if name not in by_name:
                raise UserError(_('No folder named "%s".') % name)
            self._refuse_if_load_bearing(conn, account, name, _('deleted'))
            delimiter = by_name[name]['delimiter'] or '/'
            children = sorted(n for n in by_name if n.startswith(name + delimiter))
            if children:
                raise UserError(_(
                    '"%(name)s" still has folders inside it (%(children)s). Delete '
                    'those first, innermost one first.',
                    name=name, children=', '.join(children[:5]),
                ))
            held = self._message_count(conn, name)
            if held:
                raise UserError(_(
                    '"%(name)s" still holds %(count)s message(s), and deleting a folder '
                    'takes its mail with it — there is no Trash for that. Empty it '
                    'first: delete the messages (they go to Trash and can be fished '
                    'back out) or move them somewhere else.',
                    name=name, count=held,
                ))
            # Deleting the folder you are standing in is undefined enough that
            # servers disagree; step back to INBOX first.
            conn.select('INBOX', readonly=True)
            typ, data = conn.delete(self._quote(name))
            if typ != 'OK':
                raise UserError(_(
                    'The server refused to delete "%(name)s": %(error)s',
                    name=name, error=self._response_text(data),
                ))
            return name

    @api.model
    def _message_count(self, conn, name):
        """How many messages a folder holds, for the emptiness check above."""
        try:
            typ, data = conn.status(self._quote(name), '(MESSAGES)')
            if typ == 'OK':
                match = re.search(r'MESSAGES\s+(\d+)', self._response_text(data))
                if match:
                    return int(match.group(1))
        except (imaplib.IMAP4.error, OSError):
            pass
        # A server that will not answer STATUS is not one to take an empty
        # folder on trust from: count the uids by hand instead.
        typ, _data = conn.select(self._quote(name), readonly=True)
        if typ != 'OK':
            raise UserError(_('Could not open IMAP folder "%s".') % name)
        typ, data = conn.uid('SEARCH', None, 'ALL')
        return len((data[0] or b'').split()) if typ == 'OK' else 0

    @api.model
    def _response_text(self, data):
        """The server's own words out of an imaplib response, for an error."""
        parts = []
        for entry in data or []:
            if isinstance(entry, bytes):
                parts.append(entry.decode(errors='replace'))
            elif isinstance(entry, tuple):
                parts.append(' '.join(
                    p.decode(errors='replace') if isinstance(p, bytes) else str(p)
                    for p in entry))
            else:
                parts.append(str(entry))
        return ' '.join(parts).strip()

    # ---- searching ----------------------------------------------------------

    @api.model
    def search_messages(self, account, mailbox, folder=FOLDER_INBOX, query=None,
                        sender=None, unread_only=False, flagged_only=False,
                        has_attachment=False, limit=50):
        """Search one folder, newest first (see contract).

        `query` is split into words and ANDed as separate TEXT keys rather than
        sent as one string. A literal is the wrong default here: IMAP's TEXT is
        a substring of the raw message, so a contact's name misses a signature
        that folded a header or spelled it "Klooster, Iris" — and RFC 3501
        explicitly lets a server "implement flexible matching" for TEXT, so one
        host word-matches and the next does strict substring. Separate keys
        defeat none of those and mean the same thing everywhere.

        `has_attachment` is the one term that cannot be answered exactly: IMAP
        has no such key, so it narrows on the Content-Type header and stays the
        server's word. Everything else is the server's own SEARCH.
        """
        limit = max(1, min(int(limit or 50), 200))
        criteria = []
        for word in str(query or '').split():
            criteria.extend(['TEXT', self._quote(word)])
        if sender:
            criteria.extend(['FROM', self._quote(str(sender))])
        if unread_only:
            criteria.append('UNSEEN')
        if flagged_only:
            criteria.append('FLAGGED')
        if has_attachment:
            criteria.extend(['HEADER', 'Content-Type', self._quote('multipart/mixed')])
        if not criteria:
            criteria = ['ALL']

        with self._imap(account) as conn:
            name, uidvalidity = self._select(conn, account, folder)
            typ, data = conn.uid('SEARCH', None, *criteria)
            if typ != 'OK':
                raise UserError(_('IMAP search failed in folder "%s".') % name)
            uids = (data[0] or b'').split()
            if not uids:
                return []
            # SEARCH answers ascending, which is arrival order, so the newest
            # are the tail — and newest first is what a person searching wants.
            uids = uids[-limit:]
            typ, data = conn.uid('FETCH', b','.join(uids),
                                 '(UID FLAGS INTERNALDATE BODY.PEEK[HEADER])')
            if typ != 'OK':
                raise UserError(_('Could not read messages from folder "%s".') % name)
            # The reference is built from the folder ARGUMENT, not from the
            # name it resolved to, so a message the sync found and a message
            # this found carry the same provider_message_id.
            messages = [
                self._normalize_message(item, folder, uidvalidity)
                for item in self._parse_fetch(data)
            ]
        messages.sort(key=lambda m: m['date'] or datetime.min, reverse=True)
        return messages

    # ---- message state ------------------------------------------------------

    @api.model
    def unread_message_ids(self, account, mailbox, folder=FOLDER_INBOX,
                           limit=UNREAD_CAP):
        """The unread message references in one folder (see contract).

        The reference is built from the folder ARGUMENT and the folder's
        current UIDVALIDITY, exactly as the sync builds it, so a handle from
        here and a handle the sync stored are the same string. A renumbered
        folder therefore matches nothing rather than matching the wrong mail.
        """
        limit = max(1, int(limit or UNREAD_CAP))
        with self._imap(account) as conn:
            name, uidvalidity = self._select(conn, account, folder)
            typ, data = conn.uid('SEARCH', None, 'UNSEEN')
            if typ != 'OK':
                raise UserError(
                    _('Could not read the unread mail in folder "%s".') % name)
            # SEARCH answers ascending, so the tail is the newest.
            uids = (data[0] or b'').split()[-limit:]
        return [self._message_ref(folder, uidvalidity, uid.decode())
                for uid in uids]

    @api.model
    def set_seen(self, account, mailbox, provider_message_ids, seen=True):
        """Mark messages read or unread (see contract)."""
        return self._store(account, provider_message_ids, '\\Seen', seen)

    @api.model
    def set_flagged(self, account, mailbox, provider_message_ids, flagged=True):
        """Star messages or unstar them (see contract)."""
        return self._store(account, provider_message_ids, '\\Flagged', flagged)

    def _store(self, account, provider_message_ids, marker, on):
        """A raw UID STORE, and deliberately nothing after it.

        No EXPUNGE. Marking is meant to be the one mail write a person can
        undo, so it does not get to delete anything as a side effect — and an
        EXPUNGE here would permanently remove whatever another mail client left
        marked \\Deleted in the folder. The \\Seen sweep is where that bites
        hardest: marking an inbox read touches every message in it.
        """
        grouped = self._group_refs(provider_message_ids)
        if not grouped:
            return 0
        count = 0
        with self._imap(account) as conn:
            for folder, uids in grouped.items():
                name, current = self._select(conn, account, folder, readonly=False)
                uid_set = ','.join(uid for validity, uid in uids
                                   if self._valid_uidvalidity(name, validity, current))
                if not uid_set:
                    continue
                typ, data = conn.uid('STORE', uid_set,
                                     ('+' if on else '-') + 'FLAGS', f'({marker})')
                if typ != 'OK':
                    raise UserError(_(
                        'Could not update flags in "%(folder)s": %(error)s',
                        folder=name, error=self._response_text(data),
                    ))
                count += len(uid_set.split(','))
        return count

    @api.model
    def _group_refs(self, provider_message_ids):
        """Group `folder:uidvalidity:uid` references by folder, order kept."""
        if isinstance(provider_message_ids, str):
            provider_message_ids = [provider_message_ids]
        grouped = {}
        for ref in provider_message_ids or []:
            folder, uidvalidity, uid = self._parse_message_ref(ref)
            if not uid.isdigit():
                # Rejects anything that is not a bare uid, so a crafted
                # reference cannot smuggle extra IMAP into a command below.
                raise UserError(_('Not an IMAP message reference: %s') % ref)
            grouped.setdefault(folder, []).append((uidvalidity, uid))
        return grouped

    @api.model
    def _valid_uidvalidity(self, name, expected, current):
        """Refuse a uid from before the server renumbered the folder.

        A bare uid after a UIDVALIDITY change addresses a *different* message,
        so acting on one is worse than skipping it.
        """
        if expected == current:
            return True
        _logger.warning(
            '[IMAP] Folder "%s" was renumbered (UIDVALIDITY %s -> %s); '
            'skipping a message reference from before that.', name, expected, current)
        return False

    # ---- filing -------------------------------------------------------------

    @api.model
    def move_messages(self, account, mailbox, provider_message_ids, destination):
        """Move messages into `destination` (see contract).

        UID MOVE (RFC 6851) when the server has it, UID COPY plus a scoped UID
        EXPUNGE (RFC 4315) when it has UIDPLUS instead. A server with neither
        is refused rather than served: the alternatives are leaving the message
        in both folders, which is a copy wearing the word move, and a bare
        EXPUNGE, which would take another client's pending deletions with it.
        """
        grouped = self._group_refs(provider_message_ids)
        moved = []
        with self._imap(account) as conn:
            target = self._folder_name(conn, account, destination)
            # Same rule as search: a role stays a role in the reference.
            ref_folder = destination if destination in FOLDER_ROLES else target
            has_move = self._has_capability(conn, 'MOVE')
            has_uidplus = self._has_capability(conn, 'UIDPLUS')
            if not has_move and not has_uidplus:
                raise UserError(_(
                    'This IMAP server supports neither MOVE nor UIDPLUS, so a message '
                    'cannot be moved without either leaving a copy behind or expunging '
                    'the whole folder. Move it in your mail client instead.'
                ))
            for folder, uids in grouped.items():
                name, current = self._select(conn, account, folder, readonly=False)
                usable = [uid for validity, uid in uids
                          if self._valid_uidvalidity(name, validity, current)]
                if not usable:
                    continue
                uid_set = ','.join(usable)
                command = 'MOVE' if has_move else 'COPY'
                typ, data = conn.uid(command, uid_set, self._quote(target))
                if typ != 'OK':
                    raise UserError(_(
                        'Could not move mail from "%(folder)s" to "%(target)s": %(error)s',
                        folder=name, target=target, error=self._response_text(data),
                    ))
                new_uids, new_validity = self._copyuid(data, usable)
                if command == 'COPY':
                    self._remove_uids(conn, uid_set)
                moved.extend(self._message_ref(ref_folder, new_validity, uid)
                             for uid in new_uids)
        return moved

    @api.model
    def _has_capability(self, conn, name):
        """Whether the server advertised a capability in its greeting.

        imaplib collects them at login and exposes the tuple, not a lookup.
        """
        return name.upper() in {str(c).upper() for c in (conn.capabilities or ())}

    @api.model
    def _copyuid(self, data, source_uids):
        """The destination uids out of a COPYUID response (RFC 4315).

        Every server that can move a message this way says where it put it, so
        there is no guessing to do — and a caller holding the old reference is
        holding one that points at nothing.
        """
        match = _COPYUID_RE.search(self._response_text(data))
        if not match:
            raise UserError(_(
                'The server moved the mail but did not say where to; re-sync the '
                'folder to pick the messages up again.'
            ))
        validity, _source_set, destination_set = match.groups()
        new_uids = self._expand_uid_set(destination_set)
        if len(new_uids) != len(source_uids):
            raise UserError(_('The server reported a partial move; re-sync the folder.'))
        return new_uids, validity

    @api.model
    def _expand_uid_set(self, uid_set):
        """`5,7:9` -> ['5', '7', '8', '9']."""
        uids = []
        for part in (uid_set or '').split(','):
            if ':' in part:
                start, end = part.split(':', 1)
                uids.extend(str(uid) for uid in range(int(start), int(end) + 1))
            elif part:
                uids.append(part)
        return uids

    @api.model
    def _remove_uids(self, conn, uid_set):
        """Mark these uids deleted and expunge THEM — never the folder.

        `UID EXPUNGE` is the whole point: a bare EXPUNGE removes every message
        in the folder that carries \\Deleted, including the ones another mail
        client is holding for its own undo.
        """
        conn.uid('STORE', uid_set, '+FLAGS', '(\\Deleted)')
        typ, data = conn.uid('EXPUNGE', uid_set)
        if typ != 'OK':
            _logger.warning('[IMAP] UID EXPUNGE of %s failed: %s',
                            uid_set, self._response_text(data))

    @api.model
    def delete_messages(self, account, mailbox, provider_message_ids):
        """Move messages to this mailbox's Trash (see contract)."""
        grouped = self._group_refs(provider_message_ids)
        with self._imap(account) as conn:
            trash = self._folder_name(conn, account, FOLDER_TRASH)
            for folder in grouped:
                if self._folder_name(conn, account, folder) == trash:
                    raise UserError(_(
                        'Those messages are already in "%s". Emptying the trash is not '
                        'something Mail Pro does for you: move them somewhere else, or '
                        'delete them in your mail client.'
                    ) % trash)
        # The role, not the name it resolved to, so a reference from here and
        # one from `move_messages(FOLDER_TRASH)` are the same string.
        moved = self.move_messages(account, mailbox, provider_message_ids, FOLDER_TRASH)
        return moved, trash

    # ---- drafts -------------------------------------------------------------

    @api.model
    def save_draft(self, mail_record, mailbox, account, reply_context=None):
        """Store `mail_record` in the Drafts folder without sending.

        The same MIME `send_message` builds, APPENDed \\Draft \\Seen: what is
        reviewed is byte for byte what leaves, because `send_draft` sends these
        bytes rather than rebuilding them from Odoo's fields.
        """
        msg, error = self._draft_message(mail_record, mailbox, reply_context)
        if error:
            raise UserError(_('Could not save the draft: %s') % error['error'])
        with self._imap(account) as conn:
            name = self._folder_name(conn, account, FOLDER_DRAFTS)
            typ, data = conn.append(self._quote(name), '(\\Draft \\Seen)', None,
                                    msg.as_bytes())
            if typ != 'OK':
                raise UserError(_(
                    'Could not store the draft in "%(folder)s": %(error)s',
                    folder=name, error=self._response_text(data),
                ))
            return self._appended_ref(conn, account, FOLDER_DRAFTS, data,
                                      msg['Message-ID'])

    @api.model
    def update_draft(self, mail_record, mailbox, account, provider_message_id,
                     reply_context=None):
        """Replace a stored draft (see contract).

        IMAP cannot edit a message, so this is an APPEND of the new revision
        and a scoped removal of the old one, in that order: a failure in
        between leaves two drafts, which a person can sort out, rather than
        none, which they cannot.

        When the caller says nothing about threading, the replaced draft's own
        In-Reply-To and References are carried over — an edit rewrites the
        message, and a reviewed reply must not quietly become a new
        conversation at the moment it is sent.
        """
        if reply_context is None:
            reply_context = self._draft_reply_context(account, provider_message_id)
        new_ref = self.save_draft(mail_record, mailbox, account, reply_context)
        grouped = self._group_refs(provider_message_id)
        with self._imap(account) as conn:
            for folder, uids in grouped.items():
                name, current = self._select(conn, account, folder, readonly=False)
                usable = [uid for validity, uid in uids
                          if self._valid_uidvalidity(name, validity, current)]
                if usable:
                    self._remove_uids(conn, ','.join(usable))
        return new_ref

    @api.model
    def _draft_reply_context(self, account, provider_message_id):
        """The threading of a stored draft, for an edit that did not mention any."""
        try:
            item = self._fetch_one(account, provider_message_id)
        except UserError:
            _logger.warning('[IMAP] Could not read threading off draft %s',
                            provider_message_id)
            return {}
        msg = message_from_bytes(item['raw'], policy=policy.default)
        return {
            'in_reply_to': msg.get('In-Reply-To'),
            'references': (msg.get('References') or '').split(),
            'thread_id': None,
            'provider_message_id': None,
        }

    @api.model
    def send_draft(self, account, mailbox, provider_message_id):
        """Send a stored draft as it stands (see contract).

        The draft's own bytes go on the wire. Rebuilding the message from the
        fields this module models would quietly drop the multipart/related an
        inline image lives in, the In-Reply-To that makes it a reply, and any
        header another mail client wrote — what was approved has to be what
        leaves. Exactly two headers are re-stamped: Date, because a draft's is
        when it was *written* and would sort the mail above what the recipient
        has already read, and a Message-ID when the draft has none.

        Removing the draft afterwards is last and never fatal: the mail is with
        the recipient by then, so a failure there rides back in the result
        instead of reporting a delivered message as undelivered.
        """
        item = self._fetch_one(account, provider_message_id)
        msg = message_from_bytes(item['raw'], policy=policy.default)

        recipients = mime_utils.bare_addresses([
            address for header in ('To', 'Cc', 'Bcc')
            for address in (msg.get_all(header) or [])
        ])
        if not recipients:
            return {'success': False, 'error': 'This draft has no recipients.',
                    'error_code': ERROR_NO_RECIPIENTS, 'message_id': None,
                    'thread_id': None}

        del msg['Date']
        msg['Date'] = format_datetime(datetime.now(timezone.utc))
        message_id = msg.get('Message-ID')
        if not message_id:
            message_id = mime_utils.new_message_id(mailbox.email)
            msg['Message-ID'] = message_id
        # Bcc leaves the envelope, not the message: the recipients are already
        # resolved above, and the header is the one thing a blind copy may not
        # carry to anybody else.
        del msg['Bcc']

        payload_size = len(msg.as_bytes())
        try:
            with self._smtp(account, payload_size=payload_size) as conn:
                self._check_size(conn, payload_size)
                conn.send_message(
                    msg, from_addr=parseaddr(mailbox.email)[1] or mailbox.email,
                    to_addrs=recipients)
        except UserError as e:
            return {'success': False, 'error': str(e), 'error_code': None,
                    'message_id': None, 'thread_id': None}
        except (smtplib.SMTPException, OSError) as e:
            _logger.error('[SMTP] Sending draft %s from %s failed: %s',
                          provider_message_id, mailbox.email, self._error_text(e))
            return {'success': False, 'error': self._error_text(e), 'error_code': None,
                    'message_id': None, 'thread_id': None}

        self._append_to_sent(account, msg)
        self._remove_draft(account, provider_message_id)
        return {
            'success': True,
            'error': None,
            'error_code': None,
            'message_id': message_id,
            'thread_id': mime_utils.thread_key(msg, message_id),
        }

    @api.model
    def _remove_draft(self, account, provider_message_id):
        """Take the sent draft out of Drafts. Never fatal — see `send_draft`."""
        try:
            grouped = self._group_refs(provider_message_id)
            with self._imap(account) as conn:
                for folder, uids in grouped.items():
                    name, current = self._select(conn, account, folder, readonly=False)
                    usable = [uid for validity, uid in uids
                              if self._valid_uidvalidity(name, validity, current)]
                    if usable:
                        self._remove_uids(conn, ','.join(usable))
        except Exception as e:
            _logger.warning('[IMAP] Sent draft %s but could not remove it: %s',
                            provider_message_id, self._error_text(e))

    @api.model
    def _draft_message(self, mail_record, mailbox, reply_context=None):
        """Build the draft's MIME. Returns (message, error)."""
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
        return mime_utils.build_message(
            mail_record, mailbox.email, to_addrs, cc_addrs, message_id,
            reply_context=reply_context or {}), None

    @api.model
    def _appended_ref(self, conn, account, folder, data, message_id):
        """The reference of a message this client just APPENDed.

        APPENDUID (RFC 4315) says it outright. A server without UIDPLUS is
        asked for the Message-ID instead, which is ours and unique — one extra
        round trip on the servers that need it, and no guessing on either.
        """
        match = re.search(r'APPENDUID (\d+) (\d+)', self._response_text(data))
        if match:
            return self._message_ref(folder, match.group(1), match.group(2))
        name, uidvalidity = self._select(conn, account, folder)
        typ, found = conn.uid('SEARCH', None, 'HEADER', 'Message-ID',
                              self._quote(message_id or ''))
        uids = (found[0] or b'').split() if typ == 'OK' else []
        if not uids:
            raise UserError(_('Stored the draft in "%s" but could not find it again.') % name)
        return self._message_ref(folder, uidvalidity, uids[-1].decode())
