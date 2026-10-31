# -*- coding: utf-8 -*-
"""Every account says which app issued its token, and the old ones say `own`.

`pan.mail.account.registration` is new. A refresh token is bound to the
client id that issued it, and from this version the refresh reads the
account's app rather than the provider row's current choice, so the row can
move to the Pantalytics app while the accounts signed in through the
customer's own app keep refreshing with it (ARCHITECTURE.md §9.20).

So every Microsoft 365 and Google account that exists gets the app it was
signed in through, before the ORM sees the field: the customer's own (`own`),
which is the only app a sign-in could go through before 19.0.31.0.0. The one
exception is a database that already ran 19.0.31.0.0 with a row on the
Pantalytics app that has signed in through it (its `pantalytics_client_id`
is set): there the Microsoft accounts came through our app, since a row with
no own registration fields could never have issued a token of its own. IMAP
has no app and keeps NULL.
"""
import logging

from odoo.tools.sql import column_exists

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        ALTER TABLE pan_mail_account
        ADD COLUMN IF NOT EXISTS registration varchar
    """)
    # A database coming from before 19.0.31.0.0 runs that version's
    # pre-migrate first, which makes `registration` on the row but not
    # `pantalytics_client_id`: the ORM adds that one after every pre-migrate.
    # Without it nothing can have signed in through our app.
    through_app = "FALSE"
    if column_exists(cr, 'pan_mail_provider', 'pantalytics_client_id'):
        through_app = """EXISTS (
            SELECT 1 FROM pan_mail_provider row
             WHERE row.provider = 'outlook'
               AND row.registration = 'pantalytics'
               AND COALESCE(row.pantalytics_client_id, '') != '')"""
    cr.execute(f"""
        UPDATE pan_mail_account
           SET registration = CASE
                   WHEN provider = 'outlook' AND {through_app}
                   THEN 'pantalytics'
                   ELSE 'own'
               END
         WHERE registration IS NULL
           AND provider IN ('outlook', 'gmail')
    """)
    _logger.info(
        '[Mail Pro] 19.0.32.0.0: registration set on %s account(s); each keeps '
        'refreshing with the app that issued its token.', cr.rowcount,
    )
