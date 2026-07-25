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
    microsoft.graph.client / (future) google.gmail.client

A provider implementation subclasses this model with `_inherit` and implements
the abstract methods below. The rule that keeps the seam clean: nothing outside
a provider implementation may build provider URLs, import provider SDKs, or
reason about provider-specific payload shapes. Everything crossing this
boundary uses the normalized structures documented here.

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
        'has_attachments':     bool,
        'headers':             {lowercased header name: value},
        'is_read':             bool,
    }

Normalized attachment (returned by get_message_attachments)
------------------------------------------------------------
    {
        'name':       str,
        'mimetype':   str,
        'content':    bytes,      # already base64-decoded
        'is_inline':  bool,
        'content_id': str or None,
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
}

PROVIDER_SELECTION = [
    ('outlook', 'Microsoft 365'),
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

    @api.model
    def resolve_sending_user(self, mailbox, author_user=None):
        """Pick whose OAuth token should be used to send from `mailbox`.

        This is the provider-specific half of notification routing. On
        Microsoft 365 a notification mailbox sends with its owner's token and a
        shared mailbox sends with the author's own token. A provider without
        shared mailboxes resolves both cases to the delegated account instead.

        Args:
            mailbox:     the mailbox record to send from
            author_user: res.users of the message author, if known

        Returns:
            res.users: the user whose token to use, or an empty recordset.
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
    def refresh_access_token(self, user):
        """Refresh and persist `user`'s access token. Returns the new token."""
        raise NotImplementedError

    @api.model
    def get_valid_token(self, user):
        """Return a usable access token, refreshing it first if needed."""
        raise NotImplementedError

    @api.model
    def get_user_email(self, token):
        """Return the email address the token authenticates as, or None."""
        raise NotImplementedError

    @api.model
    def test_connection(self, user):
        """Verify the stored credentials still work.

        Returns:
            dict: {'success': bool, 'error': str, 'email': str, ...}
        """
        raise NotImplementedError

    # -------------------------------------------------------------------------
    # Sending
    # -------------------------------------------------------------------------

    @api.model
    def send_message(self, mail_record, mailbox, user):
        """Send one `mail.mail` and return a normalized send result.

        Implementations own everything about how the message is encoded:
        recipients, CC, regular attachments, and inline images (which Odoo
        stores as /web/image/ URLs and every provider wants differently —
        Graph takes JSON fileAttachments with contentId, Gmail wants multipart
        MIME with Content-ID parts).

        Callers only see the normalized send result documented at module level.
        """
        raise NotImplementedError

    # -------------------------------------------------------------------------
    # Receiving
    # -------------------------------------------------------------------------

    @api.model
    def fetch_messages(self, user, mailbox, folder=FOLDER_INBOX,
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
    def get_message(self, user, mailbox, provider_message_id):
        """Fetch one message in full, including headers and body."""
        raise NotImplementedError

    @api.model
    def get_message_attachments(self, user, mailbox, provider_message_id):
        """Return normalized attachments for a message.

        Attachment failures must not sink the message: implementations log and
        return an empty list rather than raising.
        """
        raise NotImplementedError
