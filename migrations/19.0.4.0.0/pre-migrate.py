# -*- coding: utf-8 -*-
"""Prepare mail_message for the communication lens.

mail_message is the largest table in essentially every production Odoo. Adding
the lens fields naively means Odoo builds four indexes while holding an
ACCESS EXCLUSIVE lock, which on a multi-million-row table is minutes of a
locked database during an upgrade.

Three things make that cheap instead:

1. ADD COLUMN with no default and no NOT NULL is metadata-only in PostgreSQL 11+.
   It does not rewrite the table, so the columns themselves are free. We create
   them here rather than letting the ORM do it, only so the indexes below can
   exist before the ORM looks for them.

2. The indexes are built CONCURRENTLY, which does not block reads or writes.
   They are created under the exact names Odoo's `_add_index` generates, so when
   the ORM initialises the fields it finds the index already present and skips
   its own locking build.

3. They are PARTIAL. On this table the overwhelming majority of rows are notes,
   system logs and Discuss messages, for which every lens column is NULL. A
   partial index covers only the rows the lens actually queries, which is a
   fraction of the size and a fraction of the write cost on every message Odoo
   creates from now on.

The same treatment is applied to the two pre-existing provider-id columns,
which shipped as full btrees over mostly-NULL columns.

CREATE INDEX CONCURRENTLY cannot run inside a transaction block. Odoo gives
migration scripts an open cursor, so each statement commits first. That is safe
here: every statement is IF NOT EXISTS and the script is idempotent, so an
interrupted upgrade can simply be retried.
"""
import logging

_logger = logging.getLogger(__name__)

COLUMNS = [
    ('x_direction', 'varchar'),
    ('x_mailbox_id', 'integer'),
    ('x_account_id', 'integer'),
    ('x_res_model_id', 'integer'),
]

# (index name, column) — names must match what Odoo's _add_index would generate.
PARTIAL_INDEXES = [
    ('mail_message_x_direction_index', 'x_direction'),
    ('mail_message_x_mailbox_id_index', 'x_mailbox_id'),
    ('mail_message_x_res_model_id_index', 'x_res_model_id'),
    ('mail_message_x_microsoft_message_id_index', 'x_microsoft_message_id'),
    ('mail_message_x_microsoft_conversation_id_index', 'x_microsoft_conversation_id'),
]


def migrate(cr, version):
    for column, column_type in COLUMNS:
        cr.execute(
            f'ALTER TABLE mail_message ADD COLUMN IF NOT EXISTS {column} {column_type}'
        )
    _logger.info('[Migration] mail_message lens columns present')

    # The two provider-id columns shipped as full btrees. Drop them so the
    # partial versions below take their place under the same names.
    for index_name in ('mail_message_x_microsoft_message_id_index',
                       'mail_message_x_microsoft_conversation_id_index'):
        cr.execute(f'DROP INDEX IF EXISTS {index_name}')

    cr.commit()

    for index_name, column in PARTIAL_INDEXES:
        try:
            cr.execute(
                f'CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name} '
                f'ON mail_message ({column}) WHERE {column} IS NOT NULL'
            )
            cr.commit()
        except Exception:
            # A concurrent build can fail and leave an INVALID index behind.
            # Never abort the upgrade for it: the ORM will build a plain index
            # itself, which is slower to create but correct.
            cr.rollback()
            _logger.warning(
                '[Migration] Concurrent build of %s failed; '
                'leaving it to the ORM', index_name, exc_info=True,
            )
