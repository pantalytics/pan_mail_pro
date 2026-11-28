# -*- coding: utf-8 -*-
"""The sync's own dates move off the mailbox row, onto `pan.mail.sync.state` (#473).

The ORM has made the table by now; this fills it with one row per mailbox from
the columns the mailbox kept them in until this version. The old columns stay
where they are and nothing reads or writes them any more: dropping a column is
the one step of an upgrade that cannot be taken back.

Idempotent: a mailbox that already has its row is left alone.
"""
import logging

from odoo.tools.sql import column_exists

_logger = logging.getLogger(__name__)

COLUMNS = ('last_sync_date', 'last_sent_sync_date', 'last_check_date')


def migrate(cr, version):
    if not version:
        return
    present = [c for c in COLUMNS if column_exists(cr, 'pan_mail_mailbox', c)]
    if not present:
        return
    columns = ', '.join(present)
    cr.execute(f"""
        INSERT INTO pan_mail_sync_state
               (mailbox_id, {columns}, create_date, write_date)
        SELECT m.id, {', '.join('m.' + c for c in present)},
               now() AT TIME ZONE 'UTC', now() AT TIME ZONE 'UTC'
          FROM pan_mail_mailbox m
         WHERE NOT EXISTS (SELECT 1 FROM pan_mail_sync_state s WHERE s.mailbox_id = m.id)
    """)
    _logger.info('[Mail Pro] 19.0.43.0.0: %s mailbox(es) got their sync state row.',
                 cr.rowcount)
