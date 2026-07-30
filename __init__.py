# -*- coding: utf-8 -*-
from . import models
from . import wizard
from . import controllers

import logging

_logger = logging.getLogger(__name__)


def _disable_smtp_servers(env):
    """
    Post-install hook. Note what it deliberately does *not* do.

    It used to disable every outgoing mail server the moment the module was
    installed. That broke the first thing an admin does after installing:
    inviting users. Mail Pro cannot send those invitations yet (no app
    registration, no notification mailbox), and with SMTP already disabled
    nothing else could either — a dead zone with no way out.

    So the SMTP takeover now happens when the first mailbox is created, which is
    the same moment Graph routing activates. See
    `x_microsoft.mailbox._activate_smtp_takeover()`. On a reinstall over an
    already-configured database the mailboxes are still there, so we do it right
    away.

    Note: Odoo 19+ passes env directly instead of (cr, registry).
    """
    if env['x_microsoft.mailbox'].with_context(active_test=False).search_count([]):
        env['x_microsoft.mailbox']._activate_smtp_takeover()
    else:
        _logger.info(
            '[Mail Pro] No mailboxes yet — leaving SMTP alone so user invitations '
            'still work. It is disabled when the first mailbox is created.'
        )

    # Enable "Use Leads" in CRM settings
    # Mail Pro requires leads for incoming email routing
    try:
        group_use_lead = env.ref('crm.group_use_lead', raise_if_not_found=False)
        group_user = env.ref('base.group_user', raise_if_not_found=False)
        if group_use_lead and group_user:
            group_user.write({'implied_ids': [(4, group_use_lead.id)]})
            _logger.info('[Mail Pro] Enabled "Use Leads" in CRM settings')
    except Exception as e:
        _logger.warning(f'[Mail Pro] Could not enable Use Leads: {e}')
