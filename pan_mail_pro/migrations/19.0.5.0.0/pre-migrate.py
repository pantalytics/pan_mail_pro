# -*- coding: utf-8 -*-
"""Give every mailbox an explicit sync mode before the column becomes required.

`x_sync_mode` gains `required=True` in this release. Odoo applies the NOT NULL
constraint itself, but it refuses — with a warning, not an error — when existing
rows are NULL, which leaves the upgrade green and the column nullable.

A NULL there is the one value that must not survive: it is not a mode anybody
chose, and the safe reading of "nobody said" is "do not import mail". The code
reads it that way too (`_syncs_incoming` is an allow-list), so this only settles
what the database stores.

Runs pre-migrate because post-migrate is too late — the constraint is applied
with the schema, in between.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("SELECT to_regclass('x_microsoft_mailbox')")
    if not cr.fetchone()[0]:
        return

    cr.execute("""
        UPDATE x_microsoft_mailbox
           SET x_sync_mode = 'none'
         WHERE x_sync_mode IS NULL
    """)
    if cr.rowcount:
        _logger.info('[Mail Pro] Set %d mailbox(es) with no sync mode to "none"', cr.rowcount)
