# -*- coding: utf-8 -*-
"""Google Workspace provider, backed by the Gmail REST API.

Thin like the Microsoft one: `gmail.client` does the wire work, this translates
at the boundary so no caller above sees a Gmail JSON key. Send and receive land
in later steps; the credential resolution and capabilities are here first
because they are what dispatch and the OAuth flow need.
"""
import logging

from odoo import models, _

_logger = logging.getLogger(__name__)


class PanMailProviderGoogle(models.AbstractModel):
    _name = 'pan.mail.provider.google'
    _inherit = 'pan.mail.provider.base'
    _description = 'Google Workspace Provider (Gmail API)'

    # -------------------------------------------------------------------------
    # Credentials
    # -------------------------------------------------------------------------
    def _account_for_user(self, user):
        return self.env['pan.mail.account']._for_user(user, 'google')

    def _get_sending_account(self, mailbox, mail):
        """Which Google credentials send `mail` from `mailbox`.

        Unlike Microsoft — where a shared mailbox is sent with the author's own
        token via SendAs — a Gmail shared mailbox is its OWN Workspace account:
        one refresh token, no user behind it, authorized once. So a shared
        mailbox sends with its service account (a pan.mail.account whose email is
        the mailbox address and whose user_id is null), and personal/notification
        mailboxes send with the owner's account. This is the shape the Phase 2
        data model was built for.
        """
        mail.ensure_one()
        if mailbox.x_mailbox_type == 'shared':
            return self._service_account(mailbox)
        return self._account_for_user(mailbox.x_owner_user_id)

    def _service_account(self, mailbox):
        """The mailbox's own Gmail account — user_id null, keyed on the address."""
        return self.env['pan.mail.account'].sudo().with_context(active_test=False).search([
            ('provider', '=', 'google'),
            ('user_id', '=', False),
            ('email', '=', mailbox.email),
        ], limit=1)

    def _supported_mailbox_types(self):
        # All three work: personal/notification via the owner's account, shared
        # via a service account. 'shared' means something different here than on
        # Microsoft (a real account, not SendAs) but the mailbox type is the same.
        return ['personal', 'shared', 'notification']

    # -------------------------------------------------------------------------
    # Sending
    # -------------------------------------------------------------------------
    def _send(self, mail, mailbox, account):
        # The client already returns the neutral shape, so this is a pass-through
        # rather than the key-translation the Graph provider does. Kept explicit
        # so the interface method has one obvious home.
        return self.env['gmail.client'].send_email(mail, mailbox, account)

    # -------------------------------------------------------------------------
    # Receiving — step 4
    # -------------------------------------------------------------------------
    def _fetch_message_previews(self, mailbox, folder, since=None, limit=50):
        raise NotImplementedError(_('Gmail incoming sync is not implemented yet.'))

    def _get_message(self, mailbox, provider_message_id):
        raise NotImplementedError(_('Gmail incoming sync is not implemented yet.'))
