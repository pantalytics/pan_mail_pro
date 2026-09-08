# -*- coding: utf-8 -*-
"""Derive the mailbox type from the owner, once, for the rows that predate it.

19.0.7.17.0 turns `mailbox_type` from a radio button into a stored compute:
personal when the address is one the owner signed in with (or their user
record carries), shared otherwise, and the notification mailbox personal
whatever its address. Nothing a provider can say answers the question better
than that -- see ARCHITECTURE.md §2.

Odoo does not recompute a stored field whose column already exists, so the
value every mailbox carries today would stay whatever somebody clicked. Most
of those agree with the rule; this recomputes them all and logs the ones that
do not, because a flip changes who may send from that address and the admin
deserves to know which ones moved.

Idempotent: the compute is a pure function of the row and its owner.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    mailboxes = env['pan.mail.mailbox'].with_context(active_test=False).search([])
    before = {m.id: m.mailbox_type for m in mailboxes}
    mailboxes._compute_mailbox_type()
    mailboxes.flush_recordset(['mailbox_type'])
    flipped = [m for m in mailboxes if m.mailbox_type != before[m.id]]
    for m in flipped:
        _logger.warning(
            '[Mail Pro] 19.0.7.17.0: mailbox %s is now %s (was %s): owner %s.',
            m.email, m.mailbox_type, before[m.id],
            m.owner_user_id.login if m.owner_user_id else '-',
        )
    _logger.info(
        '[Mail Pro] 19.0.7.17.0: mailbox type derived on %s mailbox(es), %s changed.',
        len(mailboxes), len(flipped),
    )
