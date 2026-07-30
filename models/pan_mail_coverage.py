# -*- coding: utf-8 -*-
"""Link coverage: how much of the mail we carry actually lands on a document.

This is the number that says whether the "two separate worlds" problem is
being solved, and it is the gate on building a triage queue: if almost
everything files itself correctly, a queue solves nothing and the effort
belongs elsewhere.

Deliberately measured inside Odoo and nowhere else. Sending usage telemetry
out would contradict the module's own data disclosure ("No data is sent to the
module author or any third party"), and would need to be opt-in, aggregated and
documented before it could be honest. A number the customer can read on their
own screen needs none of that.

A TransientModel rather than a stored report: this is a question you ask, not a
history you keep. Nothing is written, so nothing has to be cleaned up, and the
answer cannot go stale.
"""
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models


class PanMailCoverage(models.TransientModel):
    _name = 'pan.mail.coverage'
    _description = 'Mail Link Coverage'

    period_days = fields.Selection(
        [('30', 'Last 30 days'), ('90', 'Last 90 days'), ('365', 'Last year')],
        string='Period', default='30', required=True,
    )

    total_count = fields.Integer(string='Messages', compute='_compute_coverage')
    linked_count = fields.Integer(string='Filed on a document', compute='_compute_coverage')
    contact_only_count = fields.Integer(string='Filed on a contact only', compute='_compute_coverage')
    unlinked_count = fields.Integer(string='Not filed anywhere', compute='_compute_coverage')
    unlinked_ratio = fields.Float(string='Unfiled %', compute='_compute_coverage')

    def _period_domain(self):
        self.ensure_one()
        since = fields.Datetime.now() - relativedelta(days=int(self.period_days))
        return [('x_direction', '!=', False), ('date', '>=', since)]

    @api.depends('period_days')
    def _compute_coverage(self):
        Message = self.env['mail.message']
        for record in self:
            domain = record._period_domain()
            # sudo on purpose: this is an aggregate about the database, not a
            # window onto anyone's correspondence. No subject, sender or body is
            # exposed — only counts. The lens itself stays ACL-bound, so a user
            # who clicks through still sees only what they may see, and may find
            # fewer rows than the count promised. That is the honest trade: an
            # ACL-filtered denominator would make the ratio meaningless.
            total = Message.sudo().search_count(domain)
            unlinked = Message.sudo().search_count(
                domain + ['|', ('model', '=', False), ('res_id', '=', False)]
            )
            contact_only = Message.sudo().search_count(
                domain + [('model', '=', 'res.partner')]
            )
            record.total_count = total
            record.unlinked_count = unlinked
            record.contact_only_count = contact_only
            record.linked_count = total - unlinked
            record.unlinked_ratio = (unlinked / total * 100) if total else 0.0

    # -- drill-down -------------------------------------------------------- #

    def _open_lens(self, extra_domain, name):
        self.ensure_one()
        action = self.env['ir.actions.actions']._for_xml_id(
            'pan_mail_pro.action_mail_message_lens')
        action['name'] = name
        action['domain'] = self._period_domain() + extra_domain
        action['context'] = {}
        return action

    def action_view_unlinked(self):
        return self._open_lens(
            ['|', ('model', '=', False), ('res_id', '=', False)],
            _('Mail not filed anywhere'),
        )

    def action_view_contact_only(self):
        return self._open_lens(
            [('model', '=', 'res.partner')],
            _('Mail filed on a contact only'),
        )

    def action_view_all(self):
        return self._open_lens([], _('All communication'))
