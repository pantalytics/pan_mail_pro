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

About CONCURRENTLY and transactions
-----------------------------------
CREATE INDEX CONCURRENTLY cannot run inside a transaction block, and `cr.commit()`
is not enough to escape one: psycopg2 opens a fresh transaction on the very next
statement. The only way to issue the statement at all is to put the underlying
connection in autocommit mode for the duration, which is what `_autocommit()`
below does.

If that toggle is not possible, the indexes are built the ordinary locking way
instead. That is a real cost on a large table, so it is logged as a warning
rather than passed over: the partial shape still applies, only the online build
is lost.

Every step is idempotent, so an interrupted upgrade can simply be retried. An
interrupted concurrent build leaves an INVALID index behind — never used by the
planner but still maintained on every write, and its name would make a retry's
IF NOT EXISTS a silent no-op — so invalid leftovers are dropped before each
attempt.
"""
import logging
from contextlib import contextmanager

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


@contextmanager
def _autocommit(cr):
    """Run the block with the cursor's connection in autocommit mode.

    Yields True when the switch succeeded, False when it did not — the caller
    decides what to do without the guarantee. Odoo's cursor wraps psycopg2's
    connection but exposes it, and the attribute name has moved around between
    versions, hence the lookup.
    """
    cnx = getattr(cr, '_cnx', None) or getattr(cr, 'connection', None)
    if cnx is None:
        yield False
        return
    try:
        cr.commit()  # autocommit cannot be toggled mid-transaction
        cnx.autocommit = True
    except Exception:
        _logger.warning('[Migration] Could not switch the connection to autocommit',
                        exc_info=True)
        yield False
        return
    try:
        yield True
    finally:
        try:
            cnx.autocommit = False
        except Exception:
            _logger.warning('[Migration] Could not restore autocommit=False',
                            exc_info=True)


def _drop_if_invalid(cr, index_name):
    """Remove an index left INVALID by an interrupted concurrent build."""
    cr.execute(
        """SELECT 1 FROM pg_class c
             JOIN pg_index i ON i.indexrelid = c.oid
            WHERE c.relname = %s AND NOT i.indisvalid""",
        (index_name,),
    )
    if cr.fetchone():
        _logger.warning('[Migration] %s exists but is INVALID; dropping it before '
                        'rebuilding', index_name)
        cr.execute(f'DROP INDEX IF EXISTS {index_name}')


def _create_partial_index(cr, index_name, column, concurrently):
    keyword = 'CONCURRENTLY ' if concurrently else ''
    try:
        _drop_if_invalid(cr, index_name)
        cr.execute(
            f'CREATE INDEX {keyword}IF NOT EXISTS {index_name} '
            f'ON mail_message ({column}) WHERE {column} IS NOT NULL'
        )
    except Exception:
        # Never abort the upgrade for an index: the ORM will build a plain one
        # itself, which is slower to create but correct.
        cr.rollback()
        _logger.warning('[Migration] Could not build %s; leaving it to the ORM',
                        index_name, exc_info=True)
        try:
            _drop_if_invalid(cr, index_name)
        except Exception:
            cr.rollback()
        return
    if not concurrently:
        cr.commit()


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

    with _autocommit(cr) as concurrently:
        if not concurrently:
            _logger.warning(
                '[Migration] Building the partial indexes with an ACCESS EXCLUSIVE '
                'lock: CONCURRENTLY is unavailable without autocommit. On a large '
                'mail_message this blocks writes for the duration of the build.'
            )
        for index_name, column in PARTIAL_INDEXES:
            _create_partial_index(cr, index_name, column, concurrently)

    _logger.info('[Migration] mail_message partial indexes built (%s)',
                 'concurrently' if concurrently else 'with a lock')
