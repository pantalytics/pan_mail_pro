# -*- coding: utf-8 -*-
"""One ladder instead of two switches and a scope.

19.0.7.6.0 split `sync_mode` into `sync_received`, `sync_received_scope` and
`sync_sent`. Three independent answers made eight combinations, and two of them
left half a conversation on the record: reading Sent Items without reading the
inbox, and syncing new mail while the owner's own answers stayed out. The
mailbox now asks one question, `sync_level`, whose four answers nest:

    sync_received  sync_sent  sync_received_scope   sync_level
    False          False      *                     replies
    False          True       *                     both
    True           *          known_partners        contacts
    True           *          all                   everyone

One row is not behaviour-preserving. `sync_received` without `sync_sent` used
to leave the Sent folder unread; `contacts` reads it, for the owner's replies.
Mapping it to `both` instead would have stopped the new incoming mail the
customer explicitly asked for, so the rung that keeps what enters wins, and the
Sent folder comes with it. The migration names each mailbox it does this to.

**Why pre-migrate.** Removing a field from Python orphans its `ir.model.data`
row, and `_process_end` unlinks orphans with the uninstall flag set, which drops
the column. Filling the new column here means the old three are still there to
read, and the ORM finds `sync_level` in place when it loads.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        SELECT column_name FROM information_schema.columns
         WHERE table_name = 'pan_mail_mailbox'
           AND column_name IN ('sync_received', 'sync_sent', 'sync_received_scope')
    """)
    present = {row[0] for row in cr.fetchall()}
    if not present:
        _logger.info('[Mail Pro] 19.0.8.0.0: no sync switches to fold, nothing to do.')
        return

    cr.execute('ALTER TABLE pan_mail_mailbox ADD COLUMN IF NOT EXISTS sync_level varchar')

    # Say which mailboxes change behaviour, before the column that proves it goes.
    cr.execute("""
        SELECT email FROM pan_mail_mailbox
         WHERE sync_level IS NULL
           AND COALESCE(sync_received, FALSE) = TRUE
           AND COALESCE(sync_sent, FALSE) = FALSE
    """)
    for (email,) in cr.fetchall():
        _logger.warning(
            '[Mail Pro] 19.0.8.0.0: %s synced new incoming mail but not the Sent '
            'folder; it now reads both, so the owner\'s answers join the record.',
            email,
        )

    # Idempotent: only rows that have not been mapped yet are touched.
    cr.execute("""
        UPDATE pan_mail_mailbox
           SET sync_level = CASE
                 WHEN COALESCE(sync_received, FALSE)
                      AND sync_received_scope = 'all'          THEN 'everyone'
                 WHEN COALESCE(sync_received, FALSE)            THEN 'contacts'
                 WHEN COALESCE(sync_sent, FALSE)                THEN 'both'
                 ELSE 'replies'
               END
         WHERE sync_level IS NULL
    """)
    _logger.info(
        '[Mail Pro] 19.0.8.0.0: folded the sync switches into sync_level on '
        '%s mailbox(es).', cr.rowcount,
    )
