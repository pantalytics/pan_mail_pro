# -*- coding: utf-8 -*-
"""`last_check_date` is read from the sync runs now, not written on the mailbox (#471).

The column stays where it was and nothing writes it any more. A mailbox that
has run in the last day already has the run the compute reads; one whose last
check is older (sync off, or a cron that stopped) gets one finished run at the
old value, so the form keeps saying since when it was last read.
"""
import logging

from odoo.tools.sql import column_exists

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version or not column_exists(cr, 'pan_mail_mailbox', 'last_check_date'):
        return
    cr.execute("""
        INSERT INTO pan_mail_sync_run
               (mailbox_id, kind, started, state, read_count, linked_count,
                skipped_count, error_count, create_date, write_date)
        SELECT m.id, 'incoming', m.last_check_date, 'done', 0, 0, 0, 0,
               now() AT TIME ZONE 'UTC', now() AT TIME ZONE 'UTC'
          FROM pan_mail_mailbox m
         WHERE m.last_check_date IS NOT NULL
           AND NOT EXISTS (
                SELECT 1 FROM pan_mail_sync_run r
                 WHERE r.mailbox_id = m.id AND r.kind = 'incoming'
                   AND r.state = 'done' AND r.started >= m.last_check_date)
    """)
    _logger.info('[Mail Pro] 19.0.42.2.0: %s mailbox(es) kept their last check as a run.',
                 cr.rowcount)
