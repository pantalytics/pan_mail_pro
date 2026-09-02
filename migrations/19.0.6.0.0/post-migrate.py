# -*- coding: utf-8 -*-
"""One index for Message-IDs; nothing provider-specific left on mail_mail.

`mail.message.x_microsoft_message_id` held the Message-ID the provider minted
on send. Since 19.0.4.0.0 that id has also gone into `pan.mail.message.ref`,
the index every lookup reads first, so the column was a second copy for new
mail and the only copy for mail sent before the index existed. The rows that
only exist here are moved into the index, then the column goes.

`mail.mail` carried the same two ids again. A `mail.mail` is deleted once it is
sent, so those were a third copy with the shortest life of all.

Runs post-migrate because the ref table has to exist, and on a database
jumping from before 19.0.4.0.0 it does not until the registry has loaded.
Both steps are idempotent: the backfill skips rows already indexed and the
drops use IF EXISTS.
"""
import logging

_logger = logging.getLogger(__name__)


def _column_exists(cr, table, column):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = %s AND column_name = %s
    """, (table, column))
    return bool(cr.fetchone())


def backfill_message_refs(cr):
    """Move provider-minted Message-IDs into `pan.mail.message.ref`."""
    if not _column_exists(cr, 'mail_message', 'x_microsoft_message_id'):
        return 0
    cr.execute("SELECT to_regclass('pan_mail_message_ref')")
    if not cr.fetchone()[0]:
        _logger.warning('[Mail Pro] pan_mail_message_ref is missing; leaving the legacy column in place')
        return None
    cr.execute("""
        INSERT INTO pan_mail_message_ref
               (message_id, mail_message_id, source,
                create_uid, create_date, write_uid, write_date)
        SELECT m.x_microsoft_message_id, m.id, 'provider', 1, now(), 1, now()
          FROM mail_message m
         WHERE m.x_microsoft_message_id IS NOT NULL
           AND m.x_microsoft_message_id <> ''
           AND m.x_microsoft_message_id IS DISTINCT FROM m.message_id
           AND NOT EXISTS (
                SELECT 1 FROM pan_mail_message_ref r
                 WHERE r.mail_message_id = m.id
                   AND r.message_id = m.x_microsoft_message_id)
    """)
    return cr.rowcount


def migrate(cr, version):
    moved = backfill_message_refs(cr)
    if moved is None:
        return
    _logger.info('[Mail Pro] Indexed %d provider Message-ID(s) from the legacy column', moved)

    cr.execute('ALTER TABLE mail_message DROP COLUMN IF EXISTS x_microsoft_message_id')
    for column in ('x_microsoft_message_id', 'x_microsoft_conversation_id'):
        cr.execute(f'ALTER TABLE mail_mail DROP COLUMN IF EXISTS "{column}"')
    _logger.info('[Mail Pro] Dropped the provider-id columns from mail_message and mail_mail')
