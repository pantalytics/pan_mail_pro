# -*- coding: utf-8 -*-
"""Link coverage: how much of the mail we carry actually lands on a document.

This is the number that says whether the "two separate worlds" problem is
being solved. It is also what decided the triage queue's fate: 19.0.4.0.0
built one, this measurement said almost everything files itself correctly,
and 19.0.7.0.0 removed it. The same question will be asked of the next
feature that proposes to catch what the matcher misses.

Measured inside Odoo, and the last 24 hours of it ride the daily heartbeat to
Pantalytics as four counts (`pan_mail_license._heartbeat_body`). That is the
one number the product is steered by across every installation, so it goes
with the counts the heartbeat already carries; it is documented in the
manifest's Data Disclosure like the rest of them. Counts only, and the same
four the customer reads on this screen: nothing leaves that they cannot see
themselves.

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
    linked_count = fields.Integer(string='Linked to a document', compute='_compute_coverage')
    contact_only_count = fields.Integer(string='On a contact only', compute='_compute_coverage')
    unlinked_count = fields.Integer(string='Linked to nothing', compute='_compute_coverage')
    unlinked_ratio = fields.Float(string='Not linked', compute='_compute_coverage')

    @api.model
    def _contact_only_domain(self):
        """Linked to a contact and nothing else.

        `res_id` must be set, or the message is linked to nothing and belongs in that
        row instead — the three rows have to stay disjoint.
        """
        return [('model', '=', 'res.partner'), ('res_id', '!=', False)]

    def _period_domain(self):
        self.ensure_one()
        since = fields.Datetime.now() - relativedelta(days=int(self.period_days))
        return [('x_direction', '!=', False), ('date', '>=', since)]

    @api.model
    def counts(self, domain):
        """The four counts for the mail `domain` selects: total, linked,
        contact_only, unlinked. The screen and the heartbeat both read this,
        so they cannot disagree about what "linked" means.

        sudo on purpose: this is an aggregate about the database, not a
        window onto anyone's correspondence. No subject, sender or body is
        exposed, only counts. The lens itself stays ACL-bound, so a user who
        clicks through still sees only what they may see, and may find fewer
        rows than the count promised. That is the honest trade: an
        ACL-filtered denominator would make the ratio meaningless.
        """
        Message = self.env['mail.message'].sudo()
        domain = list(domain)
        total = Message.search_count(domain)
        unlinked = Message.search_count(
            domain + ['|', ('model', '=', False), ('res_id', '=', False)]
        )
        contact_only = Message.search_count(domain + self._contact_only_domain())
        # The three rows are disjoint and sum to the total: a message is
        # linked to a document, on a contact only, or linked to nothing.
        # Counting the contacts inside the documents made the two rows on
        # the screen look like a sum that does not add up.
        return {
            'total': total,
            'linked': total - unlinked - contact_only,
            'contact_only': contact_only,
            'unlinked': unlinked,
        }

    @api.model
    def counts_since(self, since):
        """The heartbeat's slice: every synced mail dated after `since`."""
        return self.counts([('x_direction', '!=', False), ('date', '>=', since)])

    @api.depends('period_days')
    def _compute_coverage(self):
        for record in self:
            counts = record.counts(record._period_domain())
            record.total_count = counts['total']
            record.unlinked_count = counts['unlinked']
            record.contact_only_count = counts['contact_only']
            record.linked_count = counts['linked']
            # A fraction: the `percentage` widget multiplies by 100 itself.
            record.unlinked_ratio = (
                (counts['unlinked'] / counts['total']) if counts['total'] else 0.0
            )

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
            _('Mail linked to nothing'),
        )

    def action_view_contact_only(self):
        return self._open_lens(
            self._contact_only_domain(),
            _('Mail on a contact only'),
        )

    def action_view_all(self):
        return self._open_lens([], _('All communication'))
