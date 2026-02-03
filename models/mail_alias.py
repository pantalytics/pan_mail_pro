# -*- coding: utf-8 -*-
from odoo import api, models


class MailAlias(models.Model):
    """Extend mail.alias to customize display name."""
    _inherit = 'mail.alias'

    @api.depends('alias_name', 'alias_full_name')
    def _compute_display_name(self):
        """Show only alias name without domain for cleaner display."""
        for record in self:
            record.display_name = record.alias_name or record.alias_full_name or f"Alias #{record.id}"
