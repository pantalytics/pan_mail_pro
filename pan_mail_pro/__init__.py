# -*- coding: utf-8 -*-
from . import models
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
    `pan.mail.mailbox._activate_smtp_takeover()`. On a reinstall over an
    already-configured database the mailboxes are still there, so we do it right
    away.

    Note: Odoo 19+ passes env directly instead of (cr, registry).
    """
    if env['pan.mail.mailbox'].with_context(active_test=False).search_count([]):
        env['pan.mail.mailbox']._activate_smtp_takeover()
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


def _restore_smtp_servers(env):
    """Uninstall hook: give Odoo's own outgoing mail back.

    The takeover disabled every active `ir.mail_server` when the first mailbox
    was created and put the placeholder in front. A database that removes the
    module afterwards was left with no outgoing mail at all and no hint why:
    the module was gone, the servers stayed off. This re-enables exactly the
    servers the takeover disabled (recorded when it did) and retires the
    placeholder. A server somebody switched off on purpose stays off.
    """
    IrConfigParameter = env['ir.config_parameter'].sudo()
    MailServer = env['ir.mail_server'].sudo().with_context(active_test=False)
    disabled = IrConfigParameter.get_param('pan_mail_pro.smtp_takeover_disabled_ids', False)
    ids = [int(x) for x in (disabled or '').split(',') if x.strip().isdigit()]
    restored = MailServer.browse(ids).exists()
    if restored:
        restored.write({'active': True})
        IrConfigParameter.set_param('base_setup.default_external_email_server', 'True')
        _logger.info('[Mail Pro] Re-enabled %s outgoing mail server(s) on uninstall', len(restored))
    elif disabled is False and IrConfigParameter.get_param('pan_mail_pro.smtp_takeover_done') == 'True':
        # A takeover from before the record existed: nothing to restore by id.
        _logger.warning(
            '[Mail Pro] Uninstalled with no record of which mail server the takeover '
            'disabled: check Settings, Technical, Outgoing Mail Servers before relying on email.')
    placeholder = env.ref('pan_mail_pro.mail_server_disabled', raise_if_not_found=False)
    if placeholder:
        placeholder.write({'active': False})
    for key in ('pan_mail_pro.smtp_takeover_done', 'pan_mail_pro.smtp_takeover_disabled_ids'):
        IrConfigParameter.search([('key', '=', key)]).unlink()
