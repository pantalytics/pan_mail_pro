# -*- coding: utf-8 -*-
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Pre-fill internal domains so upgraded databases are not blocked.

    From this version on, incoming sync refuses to run without an explicit list
    of internal domains. Existing databases already sync mail and would stop
    dead on upgrade, so we derive the list from what is demonstrably theirs:
    mailbox addresses, company email addresses, alias domains.

    Deliberately not "just switch the override on". The whole point of the gate
    is that an unanswered question must not resolve to "sync everything"; a
    derived-but-wrong domain list still filters something, and the settings page
    flags mailbox domains the list does not cover.

    If nothing can be derived — no mailboxes, no company email, no alias domain
    — the list stays empty and sync stays blocked with a readable error. That is
    the correct outcome: such a database cannot have been filtering anything
    anyway, and it is one field away from working.

    Idempotent: a list that is already set is never overwritten.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    Domains = env['pan.mail.internal.domains']

    if Domains.get_domains():
        _logger.info('[Mail Pro] Internal domains already configured, leaving them alone')
        return

    if Domains.sync_internal_enabled():
        _logger.info('[Mail Pro] Internal sync explicitly enabled, nothing to pre-fill')
        return

    suggested = Domains.suggest_domains()
    if not suggested:
        _logger.warning(
            '[Mail Pro] Could not derive any internal domain. Incoming sync stays '
            'blocked until an admin fills them in under Settings → Mail Pro.'
        )
        return

    Domains.set_domains(suggested)
    _logger.info(f'[Mail Pro] Pre-filled internal domains: {", ".join(suggested)}')
