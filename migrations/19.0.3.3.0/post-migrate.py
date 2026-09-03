# -*- coding: utf-8 -*-
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def _already_configured(env):
    """Does this database already have an internal domain list?

    Reads both homes: the pre-19.0.6.4.0 config parameter, which is where a
    database arriving here still keeps it, and the rows, which is where a
    database that has already been migrated keeps it.
    """
    if env['ir.config_parameter'].sudo().get_param('pan_mail_pro.internal_domains'):
        return True
    return bool(env['pan.mail.domain'].sudo().search_count([]))


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

    The folder name tracks the manifest version, not the version this was
    written against. Odoo only runs scripts numbered *above* what is installed,
    so a folder left behind at 19.0.3.1.0 after mainline moved to 19.0.3.2.0
    would never run — and would fail silently, which is the worst way for a
    gate's escape hatch to be missing.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    # 19.0.6.4.0 renamed this model and moved the list into rows, and a database
    # crossing several releases in one upgrade runs this script against that
    # newer code — while the list it has to judge is still in the old config
    # parameter, because the script that moves it runs after this one.
    Domains = env['pan.mail.domain']

    if _already_configured(env):
        _logger.info('[Mail Pro] Internal domains already configured, leaving them alone')
        return

    # Read the parameter directly: 19.0.6.4.0 removed the opt-out and the
    # helper that answered for it, and a database crossing several releases
    # in one upgrade runs this script against that newer code.
    opted_out = env['ir.config_parameter'].sudo().get_param(
        'pan_mail_pro.sync_internal_email') in ('True', 'true', '1')
    if opted_out:
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
