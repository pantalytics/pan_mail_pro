# -*- coding: utf-8 -*-
"""The boundary: a message the sync imported notifies nobody.

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

It is also provider-neutral by construction: the rule lives on `mail.thread`
and is armed in `pan_mail_fetcher`, which Graph, Gmail and IMAP all funnel
through. There is no place for the three to disagree.

See ARCHITECTURE.md §9.10.
"""
import logging

from odoo import models

_logger = logging.getLogger(__name__)

#: Set by `pan_mail_fetcher.IMPORT_CTX` on every post the sync makes.
IMPORT_FLAG = 'pan_mail_imported'


class MailThread(models.AbstractModel):
    _inherit = 'mail.thread'

    def _notify_thread(self, message, msg_vals=False, **kwargs):
        """Skip the whole notification pass for anything the sync imported.

        Returning an empty recipient list is the same shape Odoo produces for a
        message nobody follows, so nothing downstream has to know this happened:
        no `mail.notification` rows, no `mail.mail`, no web push.

        The discriminator is the context flag, not a field on the message. A
        field cannot answer this question: `x_mailbox_id` and `x_direction` are
        written for outgoing mail too, by `mail.mail._record_sent()`, so a
        boundary keyed on either would eventually silence a message a person
        wrote from the chatter. The flag says what is actually being asked --
        "is this post an import" -- and it is set in exactly one place.

        The cost of a context flag is that it is invisible afterwards and easy
        to drop in a refactor. `tests/test_sync_sends_nothing.py` is the answer
        to that: it asserts the invariant end to end, per provider and per
        direction, so losing the flag fails the build rather than a customer.
        """
        if self.env.context.get(IMPORT_FLAG):
            _logger.debug(
                '[Incoming Mail] Suppressed notification for imported message %s',
                message.id if message else '?',
            )
            return []
        return super()._notify_thread(message, msg_vals=msg_vals, **kwargs)
