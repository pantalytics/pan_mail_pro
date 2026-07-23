# -*- coding: utf-8 -*-
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class PanMailProviderBase(models.AbstractModel):
    """Interface every email provider implements.

    A provider owns everything wire-specific: how a mail is transmitted, how
    remote messages are listed and read, and which credentials to use. Anything
    else - mailbox routing, partner matching, threading, chatter posting - is
    provider-neutral and lives outside.

    Providers return normalized data only. See `message.py` for the shape of a
    normalized message; no caller outside a provider package should ever touch
    a Graph JSON key, an IMAP UID, or any other provider-native structure.
    """
    _name = 'pan.mail.provider.base'
    _description = 'Email Provider Interface'

    # -------------------------------------------------------------------------
    # Sending
    # -------------------------------------------------------------------------
    def _send(self, mail, mailbox, account):
        """Send one email.

        Args:
            mail: mail.mail record to send
            mailbox: the mailbox to send from
            account: pan.mail.account to authenticate with

        Returns:
            dict: {
                'success': bool,
                'error': str or None,
                'error_code': str or None,  # machine-readable, e.g. 'no_recipients'
                'message_id': str or None,  # RFC5322 Message-ID
                'thread_id': str or None,   # provider thread handle, None if unsupported
            }
        """
        raise NotImplementedError

    def _get_sending_account(self, mailbox, mail):
        """Return the credentials whose token should send `mail` from `mailbox`.

        This is where providers genuinely diverge. Microsoft shared mailboxes let
        each user send with their own token (Mail.Send.Shared + SendAs), so the
        answer depends on the mail's author. Other providers may only ever have
        one set of credentials per mailbox.

        Returns:
            pan.mail.account: the account to send with, or an empty recordset
        """
        raise NotImplementedError

    def _account_for_user(self, user):
        """Return this provider's credentials for `user`, if any.

        Separate from `_get_sending_account` because the two questions are not
        the same one. "Whose credentials send this mail" is answered per mailbox
        and can land on an account with no user at all - a Gmail shared mailbox
        is its own Workspace account. "Which account holds this user's
        credentials" is what mailbox routing asks when it has already decided a
        person, and a provider without per-user credentials answers it with an
        empty recordset.

        Returns:
            pan.mail.account: the user's account, or an empty recordset
        """
        raise NotImplementedError

    # -------------------------------------------------------------------------
    # Receiving
    # -------------------------------------------------------------------------
    def _fetch_message_previews(self, mailbox, folder, since=None, limit=50):
        """List messages in `folder`, oldest first, as light previews.

        Deliberately does NOT return full messages. The caller drops already-seen
        messages by `message_id` before paying for a full fetch, which on a
        1-minute cron is most of them.

        Returns:
            list of dict, each with at least `message_id`, `provider_message_id`
            and `date` (naive UTC). Sorted ascending by `date` - the sync cursor
            depends on it.
        """
        raise NotImplementedError

    def _get_message(self, mailbox, provider_message_id):
        """Fetch and normalize one full message, attachments included.

        Returns:
            dict: a normalized message (see `message.py`)
        """
        raise NotImplementedError

    # -------------------------------------------------------------------------
    # Capabilities
    # -------------------------------------------------------------------------
    def _supported_mailbox_types(self):
        """Mailbox types this provider can serve.

        Not every provider has an answer for every type - 'shared' in particular
        means different things per provider and one may not support it at all.
        """
        return ['personal', 'shared', 'notification']
