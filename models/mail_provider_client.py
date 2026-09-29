# -*- coding: utf-8 -*-
"""
Provider-agnostic email client contract.

The module talks to email providers through exactly one interface, and that
interface is shaped by what Odoo needs — not by what any single provider's API
happens to offer:

    Odoo (mail.mail, mail.thread, res.users)
        |
        v
    mail.provider.client        <-- this contract
        |
        v
    microsoft.graph.client / google.gmail.client / imap.smtp.client

A provider implementation subclasses this model with `_inherit` and implements
the abstract methods below. The rule that keeps the seam clean: nothing outside
a provider implementation may build provider URLs, import provider SDKs, or
reason about provider-specific payload shapes. Everything crossing this
boundary uses the normalized structures documented here.

Credentials never cross it either. A provider is handed a `pan.mail.account` —
credentials for one address on one provider — and it is the provider that
decides which account applies, because that is where providers genuinely
diverge: a Microsoft shared mailbox is sent with the author's own token
(SendAs), while a Gmail shared address is its own Workspace account with no
Odoo user behind it at all, and an IMAP/SMTP address is a login with no OAuth
anywhere in sight.

Normalized message (returned by fetch_messages / get_message)
-------------------------------------------------------------
    {
        'provider_message_id': str,   # provider's own id, for follow-up calls
        'message_id':          str,   # RFC 5322 Message-ID, used for dedup
        'thread_id':           str,   # conversation/thread id, used for threading
        'subject':             str,
        'from':                {'email': str, 'name': str},
        'to':                  [{'email': str, 'name': str}, ...],
        'cc':                  [{'email': str, 'name': str}, ...],
        'date':                datetime (naive UTC),
        'body_html':           str,
        'body_is_html':        bool,
        'has_attachments':     bool,  # provider's own flag; not reliable alone.
                                      # Graph reports False for inline-only
                                      # images, so callers also sniff body_html
                                      # for 'cid:'.
        'headers':             {lowercased header name: value},
                                      # allowlisted; see HEADER_ALLOWLIST
        'is_read':             bool,
    }

Two details that are easy to get wrong and fail silently:

- `date` is NAIVE UTC. It is compared against `last_sync_date` to advance the
  sync cursor, which is naive; a tz-aware value raises at runtime instead.
- `body_html` passes through `normalize_body()` on the way out, next to
  `normalize_headers()`. A provider that calls plain text HTML hands back a
  body with newlines and no markup, which reads as one block; the helper turns
  those newlines into line breaks and leaves a real HTML body untouched.
- `headers` is an allowlist, not a copy of the message's headers. Clients build
  the full dict, use what they need locally, and hand the result to
  `normalize_headers()` on the way out. Keys are lowercased; the X-Odoo-* loop
  guard reads them and no provider guarantees header case. BCC in particular
  must never cross this boundary — see HEADER_ALLOWLIST.

Attachments are deliberately NOT part of the message. `get_message_attachments`
is a second call the caller makes only once it has decided the message is worth
keeping: the skip checks (dedup, loop guard, internal domain, block list, sync
mode) run first, and on a mailbox syncing only known contacts most messages
never survive them. Folding the fetch into `get_message` would download every
attachment of every message we are about to throw away, on a 1-minute cron.

Normalized attachment (returned by get_message_attachments)
------------------------------------------------------------
    {
        'name':       str,
        'mimetype':   str,
        'content':    bytes,      # already base64-decoded by the provider
        'is_inline':  bool,
        'content_id': str or None,   # Content-ID without angle brackets
    }

Normalized send result (returned by send_message)
--------------------------------------------------
    {
        'success':    bool,
        'error':      str or None,
        'error_code': str or None,   # see ERROR_* constants
        'message_id': str or None,   # RFC 5322 Message-ID of the sent mail
        'thread_id':  str or None,
    }

Normalized folder (returned by list_folders)
---------------------------------------------
    {
        'id':   str,             # provider handle; what folder arguments take
        'name': str,             # what the server calls it, for a human
        'role': str or None,     # one of FOLDER_ROLES, when the folder claims one
    }

The mailbox actions
-------------------
Everything above is what Odoo needs to *run* on a mailbox: send a mail, read
what came in. Everything below is what an agent or a person needs to *work* a
mailbox, and it is deliberately the same surface Squirrel (our MCP server)
exposes, method for method — one vocabulary for "what can be done to a
mailbox", so the two products cannot drift into meaning different things by
the same word:

    Squirrel tool        this contract
    -------------------  ----------------------------
    mail_list_folders    list_folders
    mail_search          search_messages
    mail_read            get_message
    mail_get_attachment  get_message_attachments
    mail_send            send_message
    mail_create_draft    save_draft
    mail_edit_draft      update_draft
    mail_send_draft      send_draft
    mail_move            move_messages
    mail_delete          delete_messages
    mail_flag            set_flagged
    mail_mark_read       set_seen
    mail_create_folder   create_folder
    mail_rename_folder   rename_folder
    mail_delete_folder   delete_folder

`mail_list_accounts` and `mail_read_chunk` have no counterpart on purpose: the
first is `pan.mail.account` here, and the second is a transport's answer to a
token budget, which Odoo does not have.

Two rules the actions inherit, and neither is negotiable in an implementation:

- **Delete is a move to Trash, and nothing more.** No expunge, no \\Deleted
  flag. The question is never "is this a write", it is "could the user not get
  this back" — and another client's pending deletions are not ours to hand to
  the next expunge. Deleting *out of* Trash is refused by name rather than
  quietly becoming an erase.
- **Marking is the one mail write that undoes itself**, so `set_seen` and
  `set_flagged` may not delete anything as a side effect. They are two methods
  rather than one with a marker argument because they are independent states,
  and a single call would have to be told which one it was *not* changing.
"""
import logging
import re
import secrets

from odoo import models, api, _
from odoo.exceptions import UserError

from .neutralization import database_is_neutralized

_logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Folder roles
#
# A folder's *name* is not something a caller gets to know. It is localized
# ("Verzonden items"), it sits under INBOX on some servers, and "Sent Items"
# and "Sent Messages" are both common — so every folder argument in this
# contract takes a ROLE from this table, or a provider folder id that
# `list_folders()` handed back. Never a name somebody typed.
#
# The value is the conventional English name, used only as the last-resort
# fallback by a provider whose server advertises nothing. IMAP reads the
# SPECIAL-USE attribute (RFC 6154), Graph and Gmail have well-known folder
# names and labels of their own.
# -----------------------------------------------------------------------------
FOLDER_INBOX = 'inbox'
FOLDER_SENT = 'sent'
FOLDER_DRAFTS = 'drafts'
FOLDER_TRASH = 'trash'
FOLDER_ARCHIVE = 'archive'
FOLDER_JUNK = 'junk'

# How many unread handles one refresh may ask a provider for. The mirror is a
# convenience, not an archive: a mailbox sitting on thousands of unread mails
# gets the answer for a bounded slice of them and keeps the rest as it found
# them, rather than paging a provider on every visit to the Inbox.
UNREAD_CAP = 500

FOLDER_ROLES = {
    FOLDER_INBOX: 'INBOX',
    FOLDER_SENT: 'Sent',
    FOLDER_DRAFTS: 'Drafts',
    FOLDER_TRASH: 'Trash',
    FOLDER_ARCHIVE: 'Archive',
    FOLDER_JUNK: 'Junk',
}

# Error codes callers may branch on. Anything else is treated as an opaque
# failure and surfaced to the user verbatim.
ERROR_NO_RECIPIENTS = 'no_recipients'
ERROR_UNSUPPORTED = 'unsupported'
# The provider asked for a pause longer than a cron run may sleep. A send
# result with this code carries `retry_after` (seconds); `mail.mail` keeps the
# mail outgoing until then instead of failing it.
ERROR_THROTTLED = 'throttled'


class ThrottledError(UserError):
    """Raised by a client's retry helper when Retry-After is too long to sleep.

    A `UserError`, so every place that shows a client's refusal to a person
    keeps working; the type is what lets the fetch cron and the send path treat
    it as "later", not "broken".
    """

    def __init__(self, message, wait):
        super().__init__(message)
        self.wait = wait

# -----------------------------------------------------------------------------
# Headers that may cross the provider boundary
#
# An allowlist, not a blocklist, and it lives here rather than in each client so
# that a provider written tomorrow inherits the rule instead of having to
# remember it.
#
# What it keeps out is BCC. A received message never carries one, so an inbox
# sync is safe by construction — but the Sent folder is what this module also
# reads, and Microsoft 365, Gmail and IMAP all preserve `Bcc` on the sender's
# own copy. What leaks there is not content but the recipient list, which is
# the entire point of a blind copy: blind-copy the company lawyer on a mail to
# a customer, and the sync would put the lawyer's address on the customer's
# record for everyone who can read it, portal users included.
#
# Stripping it downstream is not the same fix. A field that exists leaks
# eventually — through an export, the API, a report or a template — so the
# value never enters in the first place.
#
# Only add a name here once something actually reads it. Everything on this
# list is read by `pan.mail.matcher` or by the fetcher's loop guard; the rest
# of what a message needs (from, to, cc, subject, date, message-id) is already
# a normalized field of its own and does not come from here.
# -----------------------------------------------------------------------------
# The wrapper a provider puts around a body it calls HTML. Nothing in here
# says anything about the content, so it does not count as markup when we ask
# whether a body really is HTML.
BODY_WRAPPER = re.compile(
    r'</?(?:!doctype|html|head|body|meta|title|o:p)\b[^>]*>', re.IGNORECASE)
# Anything else that opens a tag or a comment.
BODY_MARKUP = re.compile(r'<[a-zA-Z/!]')
BODY_NEWLINE = re.compile(r'\r\n|\r|\n')


HEADER_ALLOWLIST = frozenset({
    'in-reply-to',
    'references',
    'x-odoo-mail-id',
    'x-odoo-message-id',
    'x-odoo-model',
    'x-odoo-record-id',
})

# -----------------------------------------------------------------------------
# Provider registry
#
# The single place that maps a provider code onto its client model. Adding a
# provider means adding one entry here plus one model implementing the contract
# below; no call site elsewhere in the module needs to change.
# -----------------------------------------------------------------------------
PROVIDER_CLIENTS = {
    'outlook': 'microsoft.graph.client',
    'gmail': 'google.gmail.client',
    'imap': 'imap.smtp.client',
}

PROVIDER_SELECTION = [
    ('outlook', 'Microsoft 365'),
    ('gmail', 'Gmail'),
    ('imap', 'IMAP / SMTP'),
]

# Where each provider's consent screen sends the browser back to. These paths
# are registered in the Azure and Google consoles by every customer, so they are
# effectively public API: change one and every existing installation breaks.
# Listed here so nothing else in the module has to build a callback URL by hand.
OAUTH_CALLBACK_PATHS = {
    'outlook': '/microsoft_oauth/callback',
    'gmail': '/google_oauth/callback',
}

# Legacy: which provider was being set up used to live in this one config
# parameter, read by the settings page, the connect link in the invitation
# email, and the mailboxes created during setup. The `pan.mail.provider` row
# (19.0.6.5.0) replaced it — a row is a better home for "which provider" once
# there are per-provider credentials to hang off it. Kept only so the
# migration that moved the parameter into that model has something to read.
PARAM_SETUP_PROVIDER = 'pan_mail_pro.setup_provider'

# Provider assumed by flows that are not yet mailbox-scoped (the OAuth connect
# flow on res.users, the settings page). When a second provider lands these
# take an explicit provider argument instead.
DEFAULT_PROVIDER = 'outlook'


def get_provider_client(env, provider_code=DEFAULT_PROVIDER):
    """Resolve a provider code to its client model."""
    model_name = PROVIDER_CLIENTS.get(provider_code)
    if not model_name:
        raise UserError(_('No email client available for provider "%s".') % provider_code)
    return env[model_name]


def get_setup_provider(env):
    """The provider this database is being set up for, if one was chosen.

    Reads the `pan.mail.provider` row rather than a config parameter — see the
    comment on `PARAM_SETUP_PROVIDER` above.
    """
    return env['pan.mail.provider'].current().provider or False


def oauth_redirect_uri(env, provider_code):
    """The absolute callback URL for `provider_code` on this database."""
    path = OAUTH_CALLBACK_PATHS.get(provider_code)
    if not path:
        raise UserError(_('Provider "%s" does not use an authorization flow.') % provider_code)
    base_url = env['ir.config_parameter'].sudo().get_param('web.base.url', '')
    return f'{base_url}{path}'


class MailProviderClient(models.AbstractModel):
    """Contract every email provider implementation must satisfy."""

    _name = 'mail.provider.client'
    _description = 'Email Provider Client'

    # -------------------------------------------------------------------------
    # Capabilities
    #
    # Providers differ in how "send as somebody else" works, and the mailbox
    # model needs to know before it lets an admin configure something that
    # cannot work. Microsoft 365 has shared mailboxes (send-as with your own
    # token, given SendAs rights); Gmail has no equivalent — there you delegate
    # an account or use a Google Group, which resolves to a different token.
    # -------------------------------------------------------------------------

    # Can a user send from another mailbox using their *own* token?
    supports_shared_mailbox = False
    # Can a user send through an account explicitly delegated to them?
    supports_delegation = False
    # Which mailbox_type values this provider can actually service.
    supported_mailbox_types = ('personal',)
    # Is there a consent screen to send somebody to? False means the credentials
    # are typed in (IMAP/SMTP), which changes what "connect" means in the UI.
    uses_oauth = True
    # Can the application registration be checked on its own, before anybody
    # has signed in? See `test_credentials()`.
    supports_credential_test = False

    @api.model
    def provider_code(self):
        """Return the `provider` selection value this client implements."""
        raise NotImplementedError

    @api.model
    def provider_label(self):
        """Human-readable provider name, used in error messages."""
        return self.provider_code()

    @api.model
    def _refuse_when_neutralized(self):
        """Stop before the network, not after the provider says no.

        An empty credential (see `encryption_utils.decrypt_value`) already makes
        a send impossible, but only by failing at the far end: staging would
        still open the connection and collect a rejection from Microsoft or
        Google on every attempt. Nothing should leave a database copy at all.

        Every implementation calls this from the one point its transport cannot
        avoid -- `get_valid_token` and `_exchange_code_for_tokens` for an OAuth
        provider, `_require_credentials` for a password one. Those are the
        chokepoints, so guarding them covers every request the client makes.
        `tests/test_provider_contract.py` holds a new provider to the same rule.
        """
        if database_is_neutralized(self.env):
            raise UserError(_(
                'This Odoo instance is neutralized (a staging or test copy), so Mail '
                'Pro will not contact %s.'
            ) % self.provider_label())

    def check_mailbox_supported(self, mailbox_type):
        """Raise if this provider cannot service the given mailbox type."""
        if mailbox_type not in self.supported_mailbox_types:
            raise UserError(_(
                '%(provider)s does not support "%(type)s" mailboxes.',
                provider=self.provider_label(),
                type=mailbox_type,
            ))

    @api.model
    def normalize_headers(self, headers):
        """Reduce a message's headers to the ones allowed past this boundary.

        Every client calls this on the way out of `_normalize_message`. Keys are
        lowercased here too, so a provider that hands back mixed case still
        satisfies the contract.
        """
        return {
            name.lower(): value
            for name, value in (headers or {}).items()
            if name and name.lower() in HEADER_ALLOWLIST
        }

    @api.model
    def normalize_body(self, content, is_html):
        """Fix a body a provider calls HTML while handing back plain text.

        Graph answers `contentType: html` for a mail that was sent as plain
        text, and the content is the text itself: newlines, no markup, at most
        an `<html><body>` wrapper around it. Stored as HTML that is one
        paragraph, so the reader gets the whole mail as a single block -- and
        nothing errors, because the body is valid HTML. It is just wrong.

        The other two providers read a MIME content type, which cannot lie the
        same way, but a text part labelled text/html has exactly this shape. So
        the check lives on the seam and every client calls it, the way they all
        call `normalize_headers()`.

        A body with any markup of its own is left alone: its line structure is
        whatever the sender's client wrote, and second-guessing that is how a
        signature ends up double spaced.

        Returns:
            tuple: (content, is_html) for the normalized message.
        """
        if not is_html or not content:
            return content, is_html
        body = BODY_WRAPPER.sub('', content)
        if BODY_MARKUP.search(body) or not BODY_NEWLINE.search(body):
            return content, is_html
        # Still HTML: the provider escaped its entities when it called it HTML,
        # so the text stays as it is and only the line breaks are added.
        return BODY_NEWLINE.sub('<br>', body.strip('\r\n')), True

    # -------------------------------------------------------------------------
    # Credential resolution
    #
    # "Whose credentials" is a provider question, not a caller question. The
    # three methods below are the only way callers get an account, and every
    # one of them may legitimately return an empty recordset — the caller
    # reports that as "not connected" rather than treating it as a bug.
    # -------------------------------------------------------------------------

    @api.model
    def account_for_user(self, user):
        """Return this provider's credentials for `user`, if any.

        Distinct from `resolve_sending_account`: this answers "which account
        holds *this person's* credentials", which is what mailbox routing asks
        once the author's default mailbox has already decided the person. A
        provider with no per-user credentials answers with an empty recordset.

        Returns:
            pan.mail.account: the user's account, or an empty recordset.
        """
        raise NotImplementedError

    @api.model
    def resolve_sending_account(self, mailbox, author_user=None):
        """Pick the credentials that should send from `mailbox`.

        This is the provider-specific half of notification routing. On
        Microsoft 365 a notification mailbox sends with its owner's token and a
        shared mailbox sends with the author's own token (SendAs). Gmail has no
        SendAs equivalent, so a shared mailbox there resolves to its own service
        account and the author never enters into it.

        Args:
            mailbox:     the mailbox record to send from
            author_user: res.users of the message author, if known. Passed in
                         rather than read from env.user because in cron context
                         env.user is the cron runner, not the sender.

        Returns:
            pan.mail.account: the account to send with, or an empty recordset.
        """
        raise NotImplementedError

    @api.model
    def resolve_receiving_account(self, mailbox):
        """Pick the credentials that should read `mailbox`.

        Returns:
            pan.mail.account: the account to read with, or an empty recordset.
        """
        raise NotImplementedError

    @api.model
    def account_is_connected(self, account):
        """Whether `account` holds credentials this provider can actually use.

        The default is the OAuth answer: a refresh token is what keeps an
        account working past the next hour. A provider that does not use OAuth
        overrides this — an IMAP/SMTP account is "connected" when it has hosts,
        a username and a password, and it has no token to refresh at all.

        This is what `pan.mail.account.connected` computes, so every caller that
        asks "does this mailbox have usable credentials" gets a provider-correct
        answer without knowing which provider it is talking to.
        """
        return bool(account.refresh_token_encrypted)

    # -------------------------------------------------------------------------
    # Authentication
    #
    # These are OAuth-shaped because two of the three providers are. A password
    # provider implements them by refusing clearly: there is no consent screen
    # to send an admin to and no token to refresh, and saying so beats an
    # AttributeError somewhere further down.
    # -------------------------------------------------------------------------

    @api.model
    def generate_oauth_state(self):
        """A CSRF nonce for one authorization round trip."""
        return secrets.token_urlsafe(32)

    @api.model
    def get_authorization_url(self, redirect_uri, state=None):
        """Return the URL to send the user to in order to grant access."""
        raise NotImplementedError

    @api.model
    def _exchange_code_for_tokens(self, authorization_code, redirect_uri):
        """Trade an OAuth authorization code for tokens.

        Returns:
            dict: {'access_token', 'refresh_token', 'token_expiry'}
        """
        raise NotImplementedError

    @api.model
    def refresh_access_token(self, account):
        """Refresh and persist `account`'s access token. Returns the new token."""
        raise NotImplementedError

    @api.model
    def get_valid_token(self, account):
        """Return a usable access token, refreshing it first if needed."""
        raise NotImplementedError

    @api.model
    def get_user_email(self, token):
        """Return the email address the token authenticates as, or None."""
        raise NotImplementedError

    @api.model
    def test_connection(self, account):
        """Verify the stored credentials still work.

        Returns:
            dict: {'success': bool, 'error': str, 'email': str, ...}
        """
        raise NotImplementedError

    @api.model
    def test_credentials(self):
        """Verify the *application registration*, before anybody signs in.

        `test_connection` asks whether one person's tokens still work, which
        nobody has yet while the registration is being typed in. This asks the
        provider whether the client id, secret and tenant it was given are the
        ones it issued -- the only question an admin can answer wrong on the
        provider form, and the one they otherwise discover at the consent
        screen with an AADSTS code and no idea which field it blames.

        Only providers with `supports_credential_test` implement it: IMAP has
        no registration to test, and Google offers no call that checks a client
        id and secret without a grant to go with them, so for Google the
        sign-in is the verification.

        Returns:
            dict: {'success': bool, 'message': str}
        """
        raise NotImplementedError

    # -------------------------------------------------------------------------
    # Sending
    # -------------------------------------------------------------------------

    @api.model
    def send_message(self, mail_record, mailbox, account, reply_context=None):
        """Send one `mail.mail` and return a normalized send result.

        Implementations own everything about how the message is encoded:
        recipients, CC, regular attachments, and inline images (which Odoo
        stores as /web/image/ URLs and every provider wants differently —
        Graph takes JSON fileAttachments with contentId, Gmail wants multipart
        MIME with Content-ID parts).

        `reply_context` (see `mail.mail._build_reply_context`) says how to send
        this mail *inside* an existing thread. It is optional and every field
        may be None: a provider uses what it can honour and ignores the rest,
        and one that honours nothing still sends — just unthreaded.

            {
                'in_reply_to':         str or None,  # parent's Message-ID
                'references':          [str, ...],   # chain, root first
                'thread_id':           str or None,  # provider thread handle
                'provider_message_id': str or None,  # parent's resource id
            }

        The split is not arbitrary. Providers that accept standard headers
        (Gmail, IMAP) thread with `in_reply_to` / `references`; Microsoft Graph
        refuses to set them — `internetMessageHeaders` takes custom `x-` headers
        only — so it threads by replying *to a message*, which is what
        `provider_message_id` is for. `thread_id` is a third, weaker form some
        APIs want alongside the headers.

        Callers only see the normalized send result documented at module level.
        """
        raise NotImplementedError

    # -------------------------------------------------------------------------
    # Receiving
    # -------------------------------------------------------------------------

    @api.model
    def fetch_messages(self, account, mailbox, folder=FOLDER_INBOX,
                       since_datetime=None, limit=50):
        """List messages in `folder`, oldest first.

        Ordering matters: the incoming processor advances its sync cursor to
        the last message it saw, so implementations must return messages sorted
        ascending by date or the cursor will skip mail.

        Args:
            folder: FOLDER_INBOX or FOLDER_SENT

        Returns:
            list[dict]: normalized messages (see module docstring). The list
            form may omit 'headers' and 'body_html'; call get_message() for
            those.
        """
        raise NotImplementedError

    @api.model
    def get_message(self, account, mailbox, provider_message_id):
        """Fetch one message in full, including headers and body."""
        raise NotImplementedError

    @api.model
    def get_message_attachments(self, account, mailbox, provider_message_id):
        """Return normalized attachments for a message.

        Attachment failures must not sink the message: implementations log and
        return an empty list rather than raising.
        """
        raise NotImplementedError

    # -------------------------------------------------------------------------
    # Folders
    #
    # A folder argument is either a role from FOLDER_ROLES or a provider folder
    # id that `list_folders()` returned. Resolving one is the provider's job —
    # Graph has well-known names, Gmail has system labels, IMAP reads the
    # SPECIAL-USE attribute — so all this contract holds is the vocabulary.
    # -------------------------------------------------------------------------

    @api.model
    def _check_folder_role(self, role):
        """Raise unless `role` is one this contract knows."""
        if role not in FOLDER_ROLES:
            raise UserError(_(
                'Unknown mail folder "%(folder)s". Use one of: %(roles)s, or a '
                'folder id from list_folders().',
                folder=role, roles=', '.join(sorted(FOLDER_ROLES)),
            ))

    @api.model
    def list_folders(self, account, mailbox):
        """List every folder in the mailbox, with the role each one claims.

        This is what replaces guessing a name. A caller told that "Archief" is
        the archive files mail there with `move_messages`; one that was not
        would pass "Archive" and land it in a folder the server invented.

        Returns:
            list[dict]: normalized folders (see module docstring).
        """
        raise NotImplementedError

    @api.model
    def create_folder(self, account, mailbox, name, parent=None):
        """Create a folder, optionally inside `parent`.

        `parent` is a folder, never a path the caller assembled: the hierarchy
        delimiter is "/" on one server and "." on the next, and a mailbox that
        keeps everything under INBOX refuses a bare top-level name.

        Creating a folder that already exists is not an error.

        Returns:
            tuple: (folder id, created) — `created` is False when it was
            already there.
        """
        raise NotImplementedError

    @api.model
    def rename_folder(self, account, mailbox, folder, new_name):
        """Rename a folder, keeping it where it is in the hierarchy.

        Implementations refuse the inbox and any folder holding a role: every
        one of them is somewhere mail is filed without anybody asking — the
        Sent copy, the Trash a delete lands in — and renaming one breaks that
        quietly.

        Returns:
            str: the new folder id.
        """
        raise NotImplementedError

    @api.model
    def delete_folder(self, account, mailbox, folder):
        """Delete an EMPTY folder.

        The one genuinely irreversible thing in this contract: deleting a
        folder takes its mail with it and no Trash catches it, which is exactly
        what `delete_messages` exists not to do. So implementations refuse a
        folder that still holds mail or sub-folders and say how many. Empty it
        the recoverable way first and the folder then goes.

        Returns:
            str: the folder id that is gone.
        """
        raise NotImplementedError

    # -------------------------------------------------------------------------
    # Searching
    # -------------------------------------------------------------------------

    @api.model
    def search_messages(self, account, mailbox, folder=FOLDER_INBOX, query=None,
                        sender=None, unread_only=False, flagged_only=False,
                        has_attachment=False, limit=50):
        """Search one folder, NEWEST first. The other half of `fetch_messages`.

        `fetch_messages` answers "what has arrived since the cursor", which is
        what the sync cron asks and the only question it may ask — oldest
        first, or the cursor skips mail. This answers "find me that message",
        which is a person's question, so it is newest first and takes terms
        instead of a date.

        The terms are structured rather than a query language on purpose.
        Squirrel parses one string because an MCP client types one; here the
        caller is Odoo code, and a grammar would be a parser in the middle of a
        seam whose whole job is that each provider compiles the same question
        into its own dialect (Graph `$search`, Gmail `q=`, IMAP SEARCH keys).

        Args:
            folder:         role or folder id (see module docstring)
            query:          free text, matched against the whole message
            sender:         narrow to one From address
            unread_only:    only messages nobody has opened
            flagged_only:   only messages somebody starred
            has_attachment: only messages carrying a file
            limit:          how many at most. Clamp it: this is the method an
                            over-eager caller materializes a mailbox with.

        Returns:
            list[dict]: normalized messages, newest first. As with
            `fetch_messages` the list form may omit 'headers' and 'body_html'.
        """
        raise NotImplementedError

    # -------------------------------------------------------------------------
    # Message state
    #
    # The two markers are siblings, and neither may delete anything. See the
    # rule in the module docstring.
    # -------------------------------------------------------------------------

    @api.model
    def unread_message_ids(self, account, mailbox, folder=FOLDER_INBOX,
                           limit=UNREAD_CAP):
        """The provider handles of the unread messages in one folder.

        The read counterpart of `set_seen`, and the whole reason Mail Pro can
        agree with Outlook without asking after every message: "what is unread"
        is one cheap query on every provider, because unread is a small set.

        Handles and nothing else. A normalized message would be a body, a
        header block and an author per hit, for an answer that is a set
        membership test -- and on Gmail it costs one extra request per message
        to build.

        Args:
            limit: at most this many handles. A mailbox that keeps thousands of
                unread mails gets a truthful subset rather than a slow answer;
                the mirror leaves the rest as it found them.

        Returns:
            list[str]: provider message ids, in no promised order.
        """
        raise NotImplementedError

    @api.model
    def set_seen(self, account, mailbox, provider_message_ids, seen=True):
        """Mark messages read, or (with seen=False) put them back to unread.

        Returns:
            int: how many messages were marked.
        """
        raise NotImplementedError

    @api.model
    def set_flagged(self, account, mailbox, provider_message_ids, flagged=True):
        """Star messages, or (with flagged=False) unstar them.

        Returns:
            int: how many messages were marked.
        """
        raise NotImplementedError

    # -------------------------------------------------------------------------
    # Filing
    # -------------------------------------------------------------------------

    @api.model
    def move_messages(self, account, mailbox, provider_message_ids, destination):
        """Move messages into `destination` (a role or a folder id).

        Returns the messages' NEW ids, in the order they were given. A move is
        not a no-op on the handle: IMAP renumbers the UID and the folder is
        part of the reference, Graph mints a new message id. A caller that kept
        the old one is holding a reference to a message that is not there.

        Archiving is this call pointed at FOLDER_ARCHIVE, and un-archiving is
        the same call back — neither gets a method of its own, because none is
        needed once a folder's role is knowable.

        Returns:
            list[str]: the new provider_message_ids.
        """
        raise NotImplementedError

    @api.model
    def delete_messages(self, account, mailbox, provider_message_ids):
        """Move messages to this mailbox's Trash.

        A mail client's delete key does not erase anything, and neither does
        this. Implementations refuse when the messages are already in Trash
        rather than quietly escalating to an erase: emptying the trash is not
        something this contract does.

        Returns:
            tuple: (list of new provider_message_ids, trash folder id)
        """
        raise NotImplementedError

    # -------------------------------------------------------------------------
    # Drafts
    #
    # A draft is a `mail.mail` here, exactly as a send is. Modelling it as a
    # second dict shape would be a second implementation of "what is an
    # outgoing message", and the two would disagree about inline images within
    # a release.
    # -------------------------------------------------------------------------

    @api.model
    def save_draft(self, mail_record, mailbox, account, reply_context=None):
        """Store `mail_record` in the mailbox's Drafts folder without sending.

        Everything `send_message` encodes is encoded here too — recipients, CC,
        attachments, inline images, and the threading in `reply_context` — so
        that what is reviewed is what leaves.

        Returns:
            str: the draft's provider_message_id.
        """
        raise NotImplementedError

    @api.model
    def update_draft(self, mail_record, mailbox, account, provider_message_id,
                     reply_context=None):
        """Replace a stored draft with `mail_record`'s current content.

        Carry the threading of the draft being replaced when `reply_context` is
        None: an edit rewrites the message, so a reviewed reply would otherwise
        turn back into a new conversation at the moment it is sent.

        Returns:
            str: the draft's provider_message_id, which may be a new one.
        """
        raise NotImplementedError

    @api.model
    def send_draft(self, account, mailbox, provider_message_id):
        """Send a stored draft as it stands, and remove it from Drafts.

        The draft is already a complete message, so it is sent — not rebuilt
        from the fields this contract happens to model. Rebuilding would
        quietly drop the multipart/related an inline image lives in, the
        In-Reply-To that makes it a reply, and any header another client wrote.
        What was approved is what leaves.

        Removing the draft afterwards is best-effort and never fatal: the mail
        is with the recipient by then, so a failure there must not be reported
        as a failed send.

        Returns:
            dict: a normalized send result (see module docstring).
        """
        raise NotImplementedError
