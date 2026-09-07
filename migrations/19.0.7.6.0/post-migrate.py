# -*- coding: utf-8 -*-
"""Drop `sync_mode` once the ORM has the three fields that replaced it.

Odoo usually drops the column itself, by unlinking the orphaned `ir.model.data`
row for a field the module no longer declares. Usually is not always -- a
database that reaches this version by a path where that row was already gone
keeps a dead column forever -- so it is dropped explicitly here, after
pre-migrate has read every value out of it.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute('ALTER TABLE pan_mail_mailbox DROP COLUMN IF EXISTS sync_mode')
    _logger.info('[Mail Pro] 19.0.7.6.0: sync_mode column removed.')
