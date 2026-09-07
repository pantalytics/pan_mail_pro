# -*- coding: utf-8 -*-
"""Split the one sync switch into two, one per direction.

`sync_mode` answered both questions at once. "Send and receive" read the inbox
*and* the Sent folder, so a customer who asked to receive mail in Odoo also got
a copy of everything their people wrote in Outlook, without ever being asked
about that separately. It also bundled "should this sync at all" with "how wide
should it cast", which is why the wide answer was one click from the off
position.

Three fields now, and the mapping is behaviour-preserving:

    sync_mode          sync_received  sync_received_scope  sync_sent
    none               False          known_partners       False
    known_partners     True           known_partners       True
    all                True           all                  True

Whatever a mailbox read yesterday, it reads today. `sync_sent` defaults to False
for a mailbox created from now on, which is the right answer there and the wrong
one for a mailbox already running.

**Why pre-migrate.** Renaming a field in Python alone orphans its `ir.model.data`
row, and `_process_end` unlinks orphans with the uninstall flag set, which drops
the column. Creating and filling the new columns here means the ORM finds them
in place when it loads, and the `sync_mode` column it drops afterwards has
already been read.
"""
import logging

_logger = logging.getLogger(__name__)

COLUMNS = (
    ('sync_received', 'boolean'),
    ('sync_received_scope', 'varchar'),
    ('sync_sent', 'boolean'),
)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'pan_mail_mailbox' AND column_name = 'sync_mode'
    """)
    if not cr.fetchone():
        _logger.info('[Mail Pro] 19.0.7.5.0: no sync_mode column, nothing to split.')
        return

    for name, sql_type in COLUMNS:
        cr.execute(
            'ALTER TABLE pan_mail_mailbox ADD COLUMN IF NOT EXISTS %s %s'
            % (name, sql_type)
        )

    # Idempotent: only rows that have not been mapped yet are touched, so a
    # re-run of the same upgrade changes nothing.
    cr.execute("""
        UPDATE pan_mail_mailbox
           SET sync_received = (sync_mode IN ('known_partners', 'all')),
               sync_sent = (sync_mode IN ('known_partners', 'all')),
               sync_received_scope = CASE WHEN sync_mode = 'all'
                                          THEN 'all' ELSE 'known_partners' END
         WHERE sync_received IS NULL
            OR sync_sent IS NULL
            OR sync_received_scope IS NULL
    """)
    _logger.info(
        '[Mail Pro] 19.0.7.5.0: split sync_mode into sync_received / '
        'sync_received_scope / sync_sent on %s mailbox(es).',
        cr.rowcount,
    )
