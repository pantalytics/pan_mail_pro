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
- `reference_count` and `thread_id` say what the matcher had to work with. A
  fallback used to be indistinguishable from "the headers arrived empty", and
  telling those two apart took four other tables and a day.

Deliberately a record of what happened, not a queue that holds mail back.
Delivery is unchanged; nothing waits for approval. A log that is wrong costs a
confusing row, while a queue that is wrong costs a customer an answer.
"""
import logging

from odoo import models, fields, api, _
from odoo.exceptions import AccessError, UserError
from odoo.fields import Domain

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

    thread_id = fields.Char(
        string='Thread Key',
        help="The handle the matcher keyed this conversation on: the "
             "provider's own thread id, or the root of the References chain "
             "for providers that have none.",
    )
    reference_count = fields.Integer(
        string='References Read',
        default=0,
        help='How many Message-IDs the In-Reply-To and References headers '
             'yielded. Zero on a reply means the headers were empty by the '
             'time the matcher saw them, which is a different failure from '
             '"no rule matched" and used to be indistinguishable from it.',
    )
    reference_ids = fields.Char(
        string='References',
        help='The chain as read off the mail, nearest ancestor first.',
    )

    candidate_count = fields.Integer(string='Candidates', default=0)
    candidates = fields.Text(
        string='Candidates Considered',
        help='Every candidate the ladder produced, best first, with the rule '
             'and confidence that produced it. This is the diagnosis: it shows '
             'what the matcher nearly chose.',
    )

    suggested_model = fields.Char(
        string='Suggested Model',
        help='The best candidate the ladder found without reaching the '
             'routing threshold. One, not a list: the screen offers a '
             'suggestion somebody accepts or ignores, and the full candidate '
             'set stays in `candidates` for whoever is debugging.',
    )
    suggested_res_id = fields.Many2oneReference(
        string='Suggested Record',
        model_field='suggested_model',
    )
    suggested_name = fields.Char(
        string='Suggestion',
        help='Display name at the time of routing, stored for the same reason '
             'as `target_name`.',
    )
    suggested_reason = fields.Char(
        string='Why That One',
        help='The candidate rule\'s own words, shown next to the suggestion.',
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
    corrected_at = fields.Datetime(
        string='Corrected',
        readonly=True,
        help='When a person linked this conversation somewhere else by hand. '
             'The rule on this row is the one they overruled, which is what '
             'says whether a rule is worth its place.',
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

    def action_open_suggestion(self):
        """Open the record the ladder nearly picked, to see whether it fits."""
        self.ensure_one()
        if not self.suggested_model or not self.suggested_res_id \
                or self.suggested_model not in self.env:
            return False
        return {
            'type': 'ir.actions.act_window',
            'res_model': self.suggested_model,
            'res_id': self.suggested_res_id,
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
        references = match.get('reference_ids') or []
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
            'thread_id': match.get('thread_id') or False,
            'reference_count': len(references),
            'reference_ids': ' '.join(references)[:512] or False,
        }
        if target_record:
            vals.update({
                'model': target_record._name,
                'res_id': target_record.id,
                'target_name': target_record.display_name,
            })
        vals.update(self._suggestion_vals(match, target_record))

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

    @api.model
    def _suggestion_vals(self, match, target_record):
        """The one candidate worth offering, if the mail did not land on it.

        Only ever the best below-threshold candidate, and only when it is
        somewhere other than where the mail actually went. A suggestion that
        repeats the destination is noise on a screen whose whole job is to
        show the cases that need a decision.
        """
        empty = {
            'suggested_model': False,
            'suggested_res_id': False,
            'suggested_name': False,
            'suggested_reason': False,
        }
        if match.get('model'):
            # The ladder settled it. There is nothing to propose.
            return empty
        for candidate in match.get('candidates') or []:
            model, res_id = candidate.get('model'), candidate.get('res_id')
            if not model or not res_id or model not in self.env:
                continue
            if target_record is not None and target_record \
                    and target_record._name == model and target_record.id == res_id:
                continue
            record = self.env[model].sudo().browse(res_id)
            if not record.exists():
                continue
            return {
                'suggested_model': model,
                'suggested_res_id': res_id,
                'suggested_name': record.display_name,
                'suggested_reason': candidate.get('reason') or False,
            }
        return empty

    # ------------------------------------------------------------------ #
    # Correcting a decision
    # ------------------------------------------------------------------ #

    @api.model
    def link_to(self, message_ids, model, res_id):
        """Move messages onto another record, and remember the correction.

        The point is not the move. The point is the thread link it writes: the
        next mail in this conversation matches at rule 3, exactly and without
        anyone being asked again. One click buys permanent correctness for a
        thread, which is the only part of triage that compounds.

        Three things it deliberately does not do.

        **It adds no followers.** A message arriving on a ticket is not a
        reason to subscribe its author to that ticket, for the same reason CC
        never creates one (ARCHITECTURE.md §3). Linking mail must not become
        a way to start notifying people.

        **It posts nothing.** A correction is bookkeeping; a chatter note
        about it would be the second copy of a fact the message itself now
        carries.

        **It moves the whole conversation, not one message.** "This is linked
        to the wrong thing" is never about a single mail in a thread, and
        leaving the rest behind splits a conversation across two records,
        which is the failure the matcher exists to prevent.

        Access is checked twice and neither check is the ACL on `mail.message`:
        the caller must be a mailbox manager, and must be allowed to write the
        destination. The write itself is `sudo`, because `mail.message.model`
        and `res_id` are not fields an ordinary user may set -- which is the
        whole reason this lives in one method instead of at a dozen call sites.
        """
        if not self.env.user.has_group('pan_mail_pro.group_mail_mailbox_manager'):
            raise AccessError(_("Linking mail to a record is for mailbox managers."))
        if not model or not res_id or model not in self.env:
            raise UserError(_("That record no longer exists."))

        record = self.env[model].browse(int(res_id))
        if not record.exists():
            raise UserError(_("That record no longer exists."))
        if not hasattr(record, 'message_post'):
            raise UserError(_("%s has no chatter to link mail to.",
                              record._description or model))
        # The destination decides. A reader who cannot write the ticket cannot
        # put somebody else's correspondence on it either.
        record.check_access('write')

        messages = self.env['mail.message'].browse(
            [int(mid) for mid in (message_ids or [])]
        ).exists()
        if not messages:
            raise UserError(_("Nothing to link."))
        # Read as the caller. A message sitting on a record they cannot open
        # is refused here rather than moved on their behalf by rights they do
        # not have.
        messages.check_access('read')
        messages = messages.filtered(lambda m: m.message_type == 'email')
        if not messages:
            raise UserError(_("Only email can be linked to a record."))

        messages.sudo().write({
            'model': model,
            'res_id': record.id,
            'record_name': record.display_name,
        })

        logs = self.sudo().search([('mail_message_id', 'in', messages.ids)])
        self._relink_threads(logs, model, record)
        logs.write({
            'reviewed': True,
            'corrected_at': fields.Datetime.now(),
            'suggested_model': False,
            'suggested_res_id': False,
            'suggested_name': False,
            'suggested_reason': False,
        })
        _logger.info(
            "[Mail Matcher] %s message(s) relinked to %s/%s by %s",
            len(messages), model, record.id, self.env.user.login,
        )
        return {'model': model, 'res_id': record.id, 'name': record.display_name}

    @api.model
    def _relink_threads(self, logs, model, record):
        """Point this conversation's thread links at the corrected record.

        The links are found through the log rows rather than re-derived from
        the mail, because the log is what recorded the handle the matcher
        actually keyed on. A conversation with no link -- one that matched on
        headers alone, or whose row has aged out -- simply has nothing to
        repoint, and the correction is still worth making for the messages.
        """
        pairs = {(log.mailbox_id.id, log.thread_id) for log in logs if log.thread_id}
        if not pairs:
            return self.env['pan.mail.thread.link']
        domain = Domain.OR([
            Domain([('mailbox_id', '=', mailbox_id), ('thread_id', '=', thread_id)])
            for mailbox_id, thread_id in pairs
        ])
        links = self.env['pan.mail.thread.link'].sudo().search(domain)
        if links:
            links.write({'model': model, 'res_id': record.id})
        return links

    # ------------------------------------------------------------------ #
    # What the rules are worth, for the heartbeat
    # ------------------------------------------------------------------ #

    @api.model
    def rule_counts_since(self, since):
        """Per rule: how often it decided a mail, and how often a person then
        linked that mail somewhere else. Rule names and integers, nothing else;
        a fallback counts under `none`, because "no rule reached the threshold"
        is the outcome the whole ladder is measured against.
        """
        Log = self.sudo()
        wins = {}
        for group in Log._read_group(
            [('date', '>=', since)], ['rule'], ['__count'],
        ):
            wins[group[0] or 'none'] = group[1]
        corrected = {}
        for group in Log._read_group(
            [('corrected_at', '>=', since)], ['rule'], ['__count'],
        ):
            corrected[group[0] or 'none'] = group[1]
        return [
            {'rule': rule, 'wins': wins.get(rule, 0), 'corrected': corrected.get(rule, 0)}
            for rule in sorted(set(wins) | set(corrected))
        ]

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
