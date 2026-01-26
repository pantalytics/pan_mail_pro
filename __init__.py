# -*- coding: utf-8 -*-
from . import models
from . import wizard
from . import controllers

import logging

_logger = logging.getLogger(__name__)


def _disable_smtp_servers(env):
    """
    Disable existing SMTP servers and native Outlook integration on module install.

    Outlook Pro sends all emails via Microsoft Graph API, so:
    1. All SMTP servers should be disabled to prevent duplicate sends
    2. Native Outlook OAuth servers (smtp_authentication='outlook') must be disabled
       to prevent the native microsoft_outlook module from intercepting emails

    This is a post_init_hook called after module installation.
    Note: Odoo 19+ passes env directly instead of (cr, registry).
    """
    # Find all mail servers NOT created by this module
    existing_servers = env['ir.mail_server'].search([
        ('smtp_host', '!=', 'invalid.outlook-pro.disabled')
    ])

    if existing_servers:
        existing_servers.write({'active': False})
        for server in existing_servers:
            # Note if this is an Outlook OAuth server
            auth_type = getattr(server, 'smtp_authentication', 'login')
            extra_info = ' (Outlook OAuth)' if auth_type == 'outlook' else ''
            _logger.info(f'[Outlook Pro] Disabled SMTP server{extra_info}: {server.name} ({server.smtp_host})')

    # Disable "Use Custom Email Servers" setting
    # This prevents native Odoo email routing from interfering
    IrConfigParameter = env['ir.config_parameter'].sudo()
    IrConfigParameter.set_param('base_setup.default_external_email_server', 'False')

    _logger.info('[Outlook Pro] Disabled "Use Custom Email Servers" setting - all emails route through Graph API')
