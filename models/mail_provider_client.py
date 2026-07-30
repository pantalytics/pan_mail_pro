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
    microsoft.graph.client / google.gmail.client

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
Odoo user behind it at all.

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
        'is_read':             bool,
    }

Two details that are easy to get wrong and fail silently:

- `date` is NAIVE UTC. It is compared against `x_last_sync_date` to advance the
  sync cursor, which is naive; a tz-aware value raises at runtime instead.
- `headers` keys are lowercased by the provider. The X-Odoo-* loop guard reads
  them, and no provider guarantees header case.

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
"""
import logging

from odoo import models, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Folder identifiers Odoo cares about. Providers map these onto their own
# vocabulary (Graph: 'Inbox'/'SentItems'; Gmail: 'INBOX'/'SENT' labels).
FOLDER_INBOX = 'inbox'
FOLDER_SENT = 'sent'

# Error codes callers may branch on. Anything else is treated as an opaque
# failure and surfaced to the user verbatim.
ERROR_NO_RECIPIENTS = 'no_recipients'

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
}

PROVIDER_SELECTION = [
    ('outlook', 'Microsoft 365'),
    ('gmail', 'Gmail'),
]

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
    # Which x_mailbox_type values this provider can actually service.
    supported_mailbox_types = ('personal',)

    @api.model
    def provider_code(self):
        """Return the `x_provider` selection value this client implements."""
        raise NotImplementedError

    @api.model
    def provider_label(self):
        """Human-readable provider name, used in error messages."""
        return self.provider_code()

    @api.model
    def check_mailbox_supported(self, mailbox_type):
        """Raise if this provider cannot service the given mailbox type."""
        if mailbox_type not in self.supported_mailbox_types:
            raise UserError(_(
                '%(provider)s does not support "%(type)s" mailboxes.',
                provider=self.provider_label(),
                type=mailbox_type,
            ))

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

    # -------------------------------------------------------------------------
    # Authentication
    # -------------------------------------------------------------------------

    @api.model
    def get_authorization_url(self, redirect_uri, state=None):
        """Return the URL to send the user to in order to grant access."""
        raise NotImplementedError

    @api.model
    def exchange_code_for_tokens(self, authorization_code, redirect_uri):
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
