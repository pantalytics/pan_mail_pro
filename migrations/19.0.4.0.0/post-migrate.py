# -*- coding: utf-8 -*-
"""Backfill the communication lens for mail that already exists.

Two deliberate choices:

1. **Driven from mail_mail, not mail_message.** Outgoing mail is the only
   history whose direction and mailbox can be known for certain, and mail_mail
   is small — Odoo deletes rows on successful send, so it holds recent and
   failed mail only. Scanning it and joining back is orders of magnitude
   cheaper than a full pass over mail_message, and it cannot be wrong.

2. **Historical incoming mail is left NULL.** It could be guessed from the
   author having no user, or from a message hanging on a partner. Both guesses
   are wrong often enough to matter, and in a screen whose entire promise is
   "this is where your mail went", a confident wrong answer is worse than a
   visibly absent one. New mail is stamped correctly from this version on; the
   lens simply starts empty for the past.

The stored compute x_res_model_id is left to Odoo, which fills it for every row
during the module update. It is derived from a column that already exists, so
unlike direction it cannot be wrong.

Batched with a commit per batch so a large database makes steady progress
instead of building one enormous transaction. Idempotent: only rows that are
still NULL are touched, so an interrupted run can be retried.
"""
import logging

_logger = logging.getLogger(__name__)

BATCH_SIZE = 50000


def migrate(cr, version):
    cr.execute("""
        SELECT MIN(mail_message_id), MAX(mail_message_id)
          FROM mail_mail
         WHERE mail_message_id IS NOT NULL
    """)
    bounds = cr.fetchone()
    if not bounds or bounds[0] is None:
        _logger.info('[Migration] No mail_mail rows to backfill the lens from')
        return

    low, high = bounds
    updated = 0
    start = low
    while start <= high:
        end = start + BATCH_SIZE
        cr.execute("""
            UPDATE mail_message m
               SET x_direction = 'outgoing',
                   x_mailbox_id = mm.x_microsoft_mailbox_id
              FROM mail_mail mm
             WHERE mm.mail_message_id = m.id
               AND m.id >= %s AND m.id < %s
               AND m.x_direction IS NULL
        """, (start, end))
        updated += cr.rowcount
        cr.commit()
        start = end

    _logger.info('[Migration] Stamped %s existing outgoing message(s) for the lens', updated)
