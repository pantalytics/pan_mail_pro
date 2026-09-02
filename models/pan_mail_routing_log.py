# -*- coding: utf-8 -*-
"""
Where did that mail end up, and why?

Better matching does not answer that question — it only makes the answer right
more often. The complaint this model exists for is not "mail lands in the wrong
place", it is "I cannot tell where mail lands at all": the routing decision was
a log line on the server, and the fallback destination was a contact's chatter,
which is technically delivered and practically invisible.

So every incoming mail the fetcher delivers gets one row here: what arrived,
where it went, which rule decided that, and what the rules it did *not* pick
had to say. Two of those columns matter more than the rest:

- `outcome` separates "threaded onto something that already existed" from
  "created something new" from "fell back to contact chatter". Those look
  identical from inside Odoo today and mean very different things.
- `needs_review` marks the two cases worth a human's time. A fallback means we
  had nowhere to put it. A *created* record that had candidates means we may
  have just opened a duplicate ticket for a conversation that was already
  running — the expensive mistake, and the silent one.

Deliberately a record of what happened, not a queue that holds mail back.
Delivery is unchanged; nothing waits for approval. A log that is wrong costs a
confusing row, while a queue that is wrong costs a customer an answer.
"""
import logging

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

# Rows are written on a one-minute cron, so they accumulate. Anything older than
# this is deleted by the daily GC unless it is still flagged for review.
DEFAULT_LOG_RETENTION_DAYS = 90


class PanMailRoutingLog(models.Model):
    _name = 'pan.mail.routing.log'
    _description = 'Incoming Mail Routing Log'
    _order = 'id desc'
    _rec_name = 'subject'

    mailbox_id = fields.Many2one(
        'pan.mail.mailbox',
        string='Mailbox',
        required=True,
        ondelete='cascade',
        index=True,
    )
    mail_message_id = fields.Many2one(
        'mail.message',
        string='Message',
        ondelete='cascade',
        index=True,
        help='The message as posted in Odoo. Empty once it has been deleted.',
    )
    date = fields.Datetime(
        string='Processed',
        default=fields.Datetime.now,
        required=True,
        index=True,
    )
    subject = fields.Char(string='Subject')
    email_from = fields.Char(string='From', index=True)
    internet_message_id = fields.Char(string='Message-ID')

    outcome = fields.Selection(
        [
            ('threaded', 'Threaded onto existing record'),
            ('created', 'New record created'),
            ('fallback', 'Contact chatter (no match)'),
            ('sent_item', 'Sent item logged on contact'),
        ],
        string='Outcome',
        required=True,
        index=True,
    )
    rule = fields.Char(
        string='Rule',
        help='Matching rule that decided this, if any. Empty means no rule '
             'reached the routing threshold.',
    )
    confidence = fields.Float(string='Confidence', digits=(3, 2))
    reason = fields.Char(string='Reason')

    model = fields.Char(string='Model', index=True)
    res_id = fields.Many2oneReference(
        string='Record',
        model_field='model',
        index=True,
    )
    target_name = fields.Char(
        string='Destination',
        help='Display name of the destination at the time of routing. Stored '
             'rather than computed so the log stays readable after the record '
             'is renamed or deleted.',
    )

    candidate_count = fields.Integer(string='Candidates', default=0)
    candidates = fields.Text(
        string='Candidates Considered',
        help='Every candidate the ladder produced, best first, with the rule '
             'and confidence that produced it. This is the diagnosis: it shows '
             'what the matcher nearly chose.',
    )

    needs_review = fields.Boolean(
        string='Needs Review',
        compute='_compute_needs_review',
        store=True,
        index=True,
    )
    reviewed = fields.Boolean(
        string='Reviewed',
        default=False,
        help='Ticked by hand once someone has looked at this row.',
    )

    @api.depends('outcome', 'candidate_count')
    def _compute_needs_review(self):
        """Flag the two outcomes a human should actually look at.

        A fallback had nowhere to go. A *created* record that had candidates is
        the expensive case: we may have opened a second ticket for a
        conversation that was already running, and nothing else in Odoo would
        ever tell you.
        """
        for log in self:
            log.needs_review = (
                log.outcome == 'fallback'
                or (log.outcome == 'created' and log.candidate_count > 0)
            )

    def action_open_target(self):
        """Open the record this mail was routed to."""
        self.ensure_one()
        if not self.model or not self.res_id or self.model not in self.env:
            return False
        return {
            'type': 'ir.actions.act_window',
            'res_model': self.model,
            'res_id': self.res_id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_mark_reviewed(self):
        self.write({'reviewed': True})

    # ------------------------------------------------------------------ #
    # Writing
    # ------------------------------------------------------------------ #

    @api.model
    def log_decision(self, mailbox, match, outcome, message=None,
                     target_record=None, subject=None, email_from=None,
                     internet_message_id=None):
        """Record one routing decision.

        Never raises. This is bookkeeping about mail that has already been
        delivered; losing a row is a gap in a report, while letting the write
        fail would roll back a message the customer is waiting on.
        """
        candidates = match.get('candidates') or []
        vals = {
            'mailbox_id': mailbox.id,
            'mail_message_id': message.id if message else False,
            'subject': subject,
            'email_from': email_from,
            'internet_message_id': internet_message_id,
            'outcome': outcome,
            'rule': match.get('rule') or False,
            'confidence': match.get('confidence') or 0.0,
            'reason': match.get('reason'),
            'candidate_count': len(candidates),
            'candidates': self._format_candidates(candidates),
        }
        if target_record:
            vals.update({
                'model': target_record._name,
                'res_id': target_record.id,
                'target_name': target_record.display_name,
            })

        try:
            # Savepoint so a failed insert cannot poison the transaction the
            # rest of this cron batch is still using.
            with self.env.cr.savepoint():
                return self.sudo().create(vals)
        except Exception:
            _logger.exception("[Mail Matcher] Could not write routing log row")
            return self.browse()

    @api.model
    def _format_candidates(self, candidates):
        """One readable line per candidate, best first."""
        if not candidates:
            return False
        return '\n'.join(
            '%s/%s — %s (%.2f): %s' % (
                c.get('model'), c.get('res_id'), c.get('rule'),
                c.get('confidence') or 0.0, c.get('reason') or '',
            )
            for c in candidates
        )

    # ------------------------------------------------------------------ #
    # Housekeeping
    # ------------------------------------------------------------------ #

    @api.model
    def _gc_routing_logs(self):
        """Delete old rows, keeping anything still waiting on a human.

        One row per incoming mail on a one-minute cron adds up, and none of it
        is worth keeping forever — the value of a routing row is highest in the
        days after it is written. Rows flagged for review survive regardless of
        age until someone ticks them off, because those are the ones somebody
        asked to keep.
        """
        days = self._retention_days()
        cutoff = fields.Datetime.subtract(fields.Datetime.now(), days=days)
        stale = self.sudo().search([
            ('date', '<', cutoff),
            '|',
            ('needs_review', '=', False),
            ('reviewed', '=', True),
        ])
        if stale:
            _logger.info("[Mail Matcher] Removing %s routing log row(s) older than %s day(s)",
                         len(stale), days)
            stale.unlink()

    @api.model
    def _retention_days(self):
        raw = self.env['ir.config_parameter'].sudo().get_param(
            'pan_mail_pro.routing_log_retention_days')
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return DEFAULT_LOG_RETENTION_DAYS
        return value if value > 0 else DEFAULT_LOG_RETENTION_DAYS
