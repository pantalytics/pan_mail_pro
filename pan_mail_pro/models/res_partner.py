# -*- coding: utf-8 -*-
from odoo import fields, models


class ResPartner(models.Model):
    """Extend res.partner with email sync blocking"""
    _inherit = 'res.partner'

    x_email_sync_blocked = fields.Boolean(
        string='Block Email Sync',
        default=False,
        help='Block incoming email sync from this contact. '
             'Emails from blocked contacts will not be imported.'
    )
