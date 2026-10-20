# -*- coding: utf-8 -*-
"""A mailbox gets a heartbeat, so the screen can stop asking the reader.

`last_sync_date` is the fetch cursor: the date of the newest message read. It
stood on the form under the label "Last synced", which is the question everyone
actually asks, and on a quiet mailbox it drifts behind the clock for as long as
nobody writes in. `last_check_date` is the run itself, written whether or not
there was anything to read.

The column is created here rather than left to the ORM so the backfill can run
before the first `health_status` is computed. Existing mailboxes are seeded with
`now()`, not with their cursor: a cursor months old would put every mailbox in
the database straight past the stale threshold and fill the Inbox with warnings
on a system that is working fine. The first cron minute replaces the seed with
the truth, and a mailbox that is genuinely not being read is stale fifteen
minutes later -- which is the right way round.

Mailboxes the cron never visits are left NULL on purpose. An empty heartbeat
reads as "not stale", so a draft or archived mailbox says nothing rather than
crying wolf about a sync nobody asked it to run.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        ALTER TABLE pan_mail_mailbox
        ADD COLUMN IF NOT EXISTS last_check_date timestamp
    """)
    cr.execute("""
        UPDATE pan_mail_mailbox
           SET last_check_date = now() AT TIME ZONE 'UTC'
         WHERE state = 'active'
           AND active = true
           AND last_check_date IS NULL
    """)
    _logger.info(
        '[Mail Pro] 19.0.17.1.0: last_check_date added, %s active mailbox(es) seeded.',
        cr.rowcount,
    )
