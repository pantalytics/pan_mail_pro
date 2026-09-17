# -*- coding: utf-8 -*-
"""Drop the three switches once the ORM has the ladder that replaced them.

Odoo usually drops the columns itself, by unlinking the orphaned `ir.model.data`
rows for fields the module no longer declares. Usually is not always, so they
are dropped explicitly here, after pre-migrate has read every value out of them.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    for column in ('sync_received', 'sync_received_scope', 'sync_sent'):
        cr.execute('ALTER TABLE pan_mail_mailbox DROP COLUMN IF EXISTS %s' % column)
    _logger.info('[Mail Pro] 19.0.8.0.0: sync_received / sync_received_scope / sync_sent removed.')
