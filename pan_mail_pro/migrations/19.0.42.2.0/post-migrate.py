# -*- coding: utf-8 -*-
"""`last_check_date` and `access_checked_date` are read from the sync runs
now, not written on the mailbox (#471).

The columns stay where they were and nothing writes them any more. A mailbox
whose newest run is older than the stored date (sync off, a cron that
stopped, or runs vacuumed) gets one finished run at the stored value, so the
form keeps saying since when, and the hourly check does not all fire at once
after the upgrade.
"""
import logging

from odoo.tools.sql import column_exists

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    for column, kind, finished in (
            ('last_check_date', 'incoming', "r.state = 'done'"),
            ('access_checked_date', 'access_check', "r.state != 'running'")):
        if not column_exists(cr, 'pan_mail_mailbox', column):
            continue
        cr.execute(f"""
            INSERT INTO pan_mail_sync_run
                   (mailbox_id, kind, started, state, read_count, linked_count,
                    skipped_count, error_count, create_date, write_date)
            SELECT m.id, %s, m.{column}, 'done', 0, 0, 0, 0,
                   now() AT TIME ZONE 'UTC', now() AT TIME ZONE 'UTC'
              FROM pan_mail_mailbox m
             WHERE m.{column} IS NOT NULL
               AND NOT EXISTS (
                    SELECT 1 FROM pan_mail_sync_run r
                     WHERE r.mailbox_id = m.id AND r.kind = %s
                       AND {finished} AND r.started >= m.{column})
        """, [kind, kind])
        _logger.info('[Mail Pro] 19.0.42.2.0: %s mailbox(es) kept their %s as a run.',
                     cr.rowcount, column)
