# -*- coding: utf-8 -*-
"""One provider row, and no `in_use` toggle.

`pan.mail.provider` shipped as a row per provider with `in_use` naming the
chosen one, so that switching providers kept the credentials of the one you
switched away from. Nobody switches back and forth: a database runs on one
provider, and the toggle was a second question on a table with one meaningful
answer. See `models/pan_mail_provider.py`.

So: keep the row that was in use, delete the rest, drop the column. A dropped
row is an application registration nothing was reading — its client secret is
re-issued from the provider's console, which is where it came from.

Which row survives, in order: the one marked `in_use`; failing that the one
with credentials; failing that the oldest. A database with one row — every
database that went through setup once — takes none of those branches.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        SELECT column_name FROM information_schema.columns
         WHERE table_name = 'pan_mail_provider' AND column_name = 'in_use'
    """)
    had_toggle = bool(cr.fetchone())

    env = api.Environment(cr, SUPERUSER_ID, {})
    Provider = env['pan.mail.provider'].sudo()
    rows = Provider.search([])

    if len(rows) > 1:
        keep = env['pan.mail.provider']
        if had_toggle:
            cr.execute("SELECT id FROM pan_mail_provider WHERE in_use IS TRUE ORDER BY id LIMIT 1")
            found = cr.fetchone()
            keep = Provider.browse(found[0]) if found else keep
        if not keep:
            keep = next((row for row in rows if row._credentials_present()), rows[0])

        dropped = rows - keep
        _logger.info(
            "[Mail Pro] Mail Pro runs on one provider (%s). Dropped the "
            "registration for %s; re-enter it from that provider's console if "
            "you switch.", keep.provider, ', '.join(dropped.mapped('provider')),
        )
        dropped.unlink()

    if had_toggle:
        cr.execute("ALTER TABLE pan_mail_provider DROP COLUMN in_use")
