# -*- coding: utf-8 -*-
"""The boundary: a message the sync created notifies nobody.

An imported mail has already reached its recipients through the provider.
Odoo sending it again is wrong in every variant and for every mix of
recipients, which is what makes this a boundary rather than a filter -- there
is no legitimate exception to argue about, so the rule can be absolute and one
override can carry it.

Two earlier attempts at the same goal failed silently, and this replaces both:

- `incoming_email_to` / `incoming_email_cc` were handed to `message_post()` as
  keyword arguments Odoo discards, so the suppression their comment described
  never ran at all.
- `mail_create_nosubscribe` was set on the `message_new()` path but not on the
  partner-chatter posts, so the sync subscribed the author of every mail it
  imported. On the sender's own contact card that means a contact following
  itself and receiving its own correspondence back by mail.

Both were guards on one route into the hazard. This one sits where every route
passes, which is why it is the only one that can be tested once.

See ARCHITECTURE.md §9.10.
"""
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class MailThread(models.AbstractModel):
    _inherit = 'mail.thread'

    def _notify_thread(self, message, msg_vals=False, **kwargs):
        """Skip the whole notification pass for anything the sync imported.

        Returning an empty recipient list is the same shape Odoo produces for a
        message nobody follows, so nothing downstream has to know this happened:
        no `mail.notification` rows, no `mail.mail`, no web push.

        The discriminator is `x_mailbox_id`, which only the fetcher sets. It has
        to be part of the `message_post()` call rather than a write afterwards,
        because by the time a later write runs the notification has already been
        computed and the envelope already exists. `pan_mail_fetcher` passes it
        with the other lens fields for exactly this reason -- moving it back out
        would disarm this override without breaking a single other test.
        """
        if self._pan_mail_is_imported(message, msg_vals):
            _logger.debug(
                '[Incoming Mail] Suppressed notification for imported message %s',
                message.id if message else '?',
            )
            return []
        return super()._notify_thread(message, msg_vals=msg_vals, **kwargs)

    @staticmethod
    def _pan_mail_is_imported(message, msg_vals=False):
        """True when this message came in through a mailbox sync.

        Both sources are checked because Odoo hands the values around twice: as
        the pending `msg_vals` dict while the message is being created, and on
        the record once it exists. Reading only one of them works right up until
        the version that stops populating it.
        """
        if msg_vals and msg_vals.get('x_mailbox_id'):
            return True
        return bool(message) and bool(message.x_mailbox_id)
