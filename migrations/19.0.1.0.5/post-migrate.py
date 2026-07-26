# -*- coding: utf-8 -*-
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """
    Reset noupdate flag on cron job so future upgrades can update it.

    The cron was originally created with noupdate="1", meaning changes
    to the XML (interval, name, code) were ignored on upgrade. Now that
    the XML uses noupdate="0", we need to flip the flag in ir_model_data
    for existing installations.
    """
    cr.execute("""
        UPDATE ir_model_data
        SET noupdate = FALSE
        WHERE module = 'pan_mail_pro'
          AND name = 'ir_cron_microsoft_fetch_incoming_mail'
          AND noupdate = TRUE
    """)
    if cr.rowcount:
        _logger.info('[Mail Pro] Reset noupdate flag on cron job for future upgrades')
