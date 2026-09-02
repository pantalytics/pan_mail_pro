# -*- coding: utf-8 -*-
"""Fill an empty internal-domain list before the gate that now needs it.

19.0.6.2.0 refuses to create a mailbox while the list is empty, and refuses to
save a list that leaves one of the company's own domains out. Every database
running with an empty list is exactly the population those gates protect --
and greeting them with an error on next login is how a safety control gets
routed around rather than fixed.

So: derive the list where it is empty, from the addresses already in the
database (mailboxes and internal users), and log what was filled. Databases
that opted out explicitly are left alone; that is a decision somebody made.

Idempotent by construction: it writes only when the list is empty, so a re-run
after an admin edited it changes nothing.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    Domains = env['pan.mail.internal.domains']

    if Domains.is_configured():
        missing = Domains.uncovered_domains()
        if missing:
            _logger.warning(
                "[Mail Pro] Internal domains are configured but incomplete. "
                "Missing: %s. Mail to or from these is being treated as "
                "customer correspondence. Add them in Settings.",
                ', '.join(missing),
            )
        return

    if Domains.sync_internal_enabled():
        _logger.info(
            "[Mail Pro] Internal domains left empty: this database opted in to "
            "syncing internal email, which is a decision, not an omission."
        )
        return

    suggested = Domains.suggest_domains()
    if not suggested:
        # Nothing to derive from means nothing to protect yet: no mailboxes and
        # no internal users. The gate will ask the admin at the right moment.
        _logger.info(
            "[Mail Pro] No internal domains configured and none derivable. "
            "The first mailbox will ask for them."
        )
        return

    Domains.set_domains(suggested)
    _logger.warning(
        "[Mail Pro] Internal domains were empty and have been filled from this "
        "database's own addresses: %s. Check them in Settings — a domain that "
        "belongs to you and is missing here has its internal mail copied into "
        "Odoo.",
        ', '.join(suggested),
    )
