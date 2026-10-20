# -*- coding: utf-8 -*-
"""Clear the way for two uniqueness constraints that never existed.

`pan.mail.provider.provider` and `pan.mail.domain.name` both declared their
uniqueness with the `_sql_constraints` list. Odoo 19 dropped support for that
form and only *warns* about it, so neither constraint was ever created — the
models have been accepting duplicates since they shipped. They are declared
with `models.Constraint` from this version on.

Adding a UNIQUE constraint to a table that already holds duplicates fails, and
Odoo swallows that failure as a log line, which would leave the constraint
absent for exactly the databases that need it. So the duplicates go first,
oldest row wins: it is the one every foreign key already points at. NULLs are
left alone -- Postgres lets a UNIQUE column hold any number of them, so they
are not duplicates as far as the constraint is concerned.
"""
import logging

_logger = logging.getLogger(__name__)

# table -> column that should have been unique
DEDUPE = {
    'pan_mail_provider': 'provider',
    'pan_mail_domain': 'name',
}


def migrate(cr, version):
    if not version:
        return

    for table, column in DEDUPE.items():
        cr.execute(
            "SELECT to_regclass(%s)", ['public.%s' % table],
        )
        if not cr.fetchone()[0]:
            continue

        cr.execute(
            """
            DELETE FROM {table}
             WHERE {column} IS NOT NULL
               AND id NOT IN (
                   SELECT MIN(id) FROM {table}
                    WHERE {column} IS NOT NULL
                    GROUP BY {column}
             )
            """.format(table=table, column=column)
        )
        if cr.rowcount:
            _logger.warning(
                '[Mail Pro] Removed %s duplicate %s row(s) so UNIQUE(%s) '
                'can be created; the oldest row of each group was kept.',
                cr.rowcount, table, column,
            )
