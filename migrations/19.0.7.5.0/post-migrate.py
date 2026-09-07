# -*- coding: utf-8 -*-
"""Split the one sync switch into two: incoming, and mail sent outside Odoo.

Until now `sync_mode` answered both questions at once. "Send and receive" read
the inbox *and* the Sent folder, so a customer who asked for incoming mail also
got a copy of everything their people wrote in Outlook, without ever being asked
about that separately.

The split is behaviour-preserving on existing mailboxes: whatever they read
yesterday, they read today. `capture_sent` defaults to False, which is the right
answer for a mailbox created from now on and the wrong one for a mailbox already
running, so every syncing mailbox is switched on here.

Written straight in SQL rather than through the ORM: it is one boolean on a
handful of rows, and the column is guaranteed to exist because Odoo loaded the
new field before post-migrate runs.
"""
import logging

_logger = logging.getLogger(__name__)

SYNCING_MODES = ('known_partners', 'all')


def migrate(cr, version):
    if not version:
        return

    cr.execute(
        """
        UPDATE pan_mail_mailbox
           SET capture_sent = TRUE
         WHERE sync_mode IN %s
           AND capture_sent IS NOT TRUE
        """,
        (SYNCING_MODES,),
    )
    _logger.info(
        '[Mail Pro] 19.0.7.5.0: kept Sent-folder capture on for %s mailbox(es) '
        'that were already syncing it.',
        cr.rowcount,
    )
