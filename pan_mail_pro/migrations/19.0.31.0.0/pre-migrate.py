# -*- coding: utf-8 -*-
"""Microsoft 365 signs in through the Pantalytics app by default, but not here.

`pan.mail.provider.registration` is new, and its default is the Pantalytics
Mail Pro app: right for a database that sets Microsoft up from now on, wrong
for one that already did. Left to the ORM, every existing row would get the
default, the next sign-in would go through an app nobody consented to, and
every refresh token issued to the customer's own app would stop working.

So the column is made here, before the ORM sees the field, and every existing
row keeps the app it has: `own` when any part of an app registration is on
it. A Microsoft row with nothing filled in yet was never usable, and gets the
default a new one would.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        ALTER TABLE pan_mail_provider
        ADD COLUMN IF NOT EXISTS registration varchar
    """)
    cr.execute("""
        UPDATE pan_mail_provider
           SET registration = CASE
                   WHEN provider = 'outlook'
                    AND COALESCE(client_id, '') = ''
                    AND COALESCE(client_secret_encrypted, '') = ''
                    AND COALESCE(tenant_id, '') = ''
                   THEN 'pantalytics'
                   ELSE 'own'
               END
         WHERE registration IS NULL
    """)
    _logger.info(
        '[Mail Pro] 19.0.31.0.0: registration set on %s provider row(s); an '
        'existing app registration stays the one in use.', cr.rowcount,
    )
