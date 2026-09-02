# -*- coding: utf-8 -*-
"""Triage queue for mail that reached Odoo but landed nowhere.

`_process_message()` used to drop mail in five places with nothing but a log
line: a duplicate, an internal domain, a blocked contact, an internal user, an
unknown sender under a restrictive sync mode. Three of those are correct and
final. Two are a decision the customer would want to see and possibly reverse,
and until now they were invisible — which is the real overview gap, larger than
anything about how filed mail is displayed.

Two rules shape this model, and both are load-bearing:

**Nothing is queued that was filtered on purpose.** A blocked contact
(`res.partner.x_email_sync_blocked`) is in practice an objection to processing;
storing that mail in a new table inverts what the flag means. Mail between
internal users is employee correspondence with no document context and no
document ACL to inherit. Internal-domain mail was excluded by configuration.
None of these become records here — not even their metadata.

**No body, no attachments.** The provider remains the source of truth and the
body is re-fetched when someone opens the item. That keeps this table small,
keeps a second copy of every email out of the database, and keeps the erasure
surface to metadata that expires on its own.
"""
import logging

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .ai.pan_mail_ai import PROMPT_VERSION, get_ai_backend

_logger = logging.getLogger(__name__)

# Above this many pending items in one mailbox, stop recording rather than
# growing without bound. A queue nobody works is a disk-space bug, not a feature.
MAX_PENDING_PER_MAILBOX = 50000

DEFAULT_RETENTION_DAYS = 30
MAX_RETENTION_DAYS = 90


class PanMailItem(models.Model):
    _name = 'pan.mail.item'
    _description = 'Incoming Mail Triage Item'
    _order = 'date desc, id desc'
    _rec_name = 'subject'

    # -- provenance -------------------------------------------------------- #
    provider_message_id = fields.Char(
        string='Provider Message ID', required=True, index='btree_not_null',
        help='Handle used to re-fetch this message from the provider.',
    )
    message_id = fields.Char(
        string='Message-ID', index='btree_not_null',
        help='RFC Message-ID. Deduplication key, because a provider handle is '
             'not stable across every provider.',
    )
    mailbox_id = fields.Many2one(
        'pan.mail.mailbox', string='Mailbox', required=True,
        ondelete='cascade', index=True,
    )
    folder = fields.Char(string='Folder')
    direction = fields.Selection(
        [('incoming', 'Incoming'), ('outgoing', 'Outgoing')],
        string='Direction', default='incoming',
    )

    # -- envelope (metadata only, deliberately no body) -------------------- #
    email_from = fields.Char(string='From')
    email_to = fields.Char(string='To')
    subject = fields.Char(string='Subject')
    date = fields.Datetime(string='Date')
    partner_id = fields.Many2one('res.partner', string='Contact', ondelete='set null')

    # -- triage ------------------------------------------------------------ #
    reason = fields.Selection([
        ('unknown_contact', 'Sender is not a contact'),
        ('queued_for_review', 'Held for review'),
        ('error', 'Processing failed'),
    ], string='Reason', required=True, index=True)
    reason_detail = fields.Char(string='Details')
    state = fields.Selection([
        ('pending', 'Pending'),
        ('imported', 'Imported'),
        ('ignored', 'Ignored'),
    ], string='Status', default='pending', required=True, index=True)
    mail_message_id = fields.Many2one(
        'mail.message', string='Imported As', ondelete='set null', readonly=True,
        help='Set once this item has been imported; the audit trail of the decision.',
    )
    expiry_date = fields.Datetime(
        string='Expires', index='btree_not_null', readonly=True,
        help='Deleted automatically after this date, whatever its status.',
    )

    # -- AI triage suggestion (advisory only) ------------------------------ #
    ai_state = fields.Selection([
        ('todo', 'Not analysed'),
        ('done', 'Analysed'),
        ('skipped', 'Skipped'),
        ('failed', 'Failed'),
    ], string='AI Status', default='todo', required=True, index='btree_not_null')
    ai_backend = fields.Char(string='AI Backend', readonly=True)
    ai_model = fields.Char(string='AI Model', readonly=True)
    ai_prompt_version = fields.Char(string='Prompt Version', readonly=True)
    ai_confidence = fields.Float(string='Confidence', readonly=True)
    ai_suggested_model = fields.Char(string='Suggested Model', readonly=True)
    ai_suggested_res_id = fields.Integer(string='Suggested Record', readonly=True)
    ai_suggested_name = fields.Char(
        string='Suggestion', compute='_compute_ai_suggested_name')
    ai_rationale = fields.Char(string='Why', readonly=True)
    ai_attempts = fields.Integer(string='AI Attempts', default=0, readonly=True)

    # models.Constraint, not _sql_constraints: Odoo 19 dropped support for the
    # list form and only *warns* about it, so the constraint would have been
    # silently absent. pan.mail.account already uses this form — the copy came
    # from older code, not from this codebase.
    _uniq_message_per_mailbox = models.Constraint(
        'UNIQUE(mailbox_id, message_id)',
        'This message is already in the triage queue for this mailbox.',
    )

    # -- lifecycle --------------------------------------------------------- #

    @api.model
    def _retention_days(self):
        param = self.env['ir.config_parameter'].sudo().get_param(
            'pan_mail_pro.item_retention_days', DEFAULT_RETENTION_DAYS)
        try:
            days = int(param)
        except (TypeError, ValueError):
            days = DEFAULT_RETENTION_DAYS
        return max(1, min(days, MAX_RETENTION_DAYS))

    @api.model_create_multi
    def create(self, vals_list):
        retention = self._retention_days()
        for vals in vals_list:
            vals.setdefault(
                'expiry_date',
                fields.Datetime.now() + relativedelta(days=retention),
            )
        return super().create(vals_list)

    @api.model
    def _record_skip(self, mailbox, message, folder, reason, detail=None,
                     partner=None, direction='incoming'):
        """Record one skipped message. Never raises, never blocks the fetcher.

        Called from `_process_message()` in place of a bare `return False`. It
        must be impossible for a triage failure to cost a mail: any error here
        is logged and swallowed, because the alternative is a fetch batch dying
        over bookkeeping.
        """
        try:
            pending = self.sudo().search_count([
                ('mailbox_id', '=', mailbox.id), ('state', '=', 'pending'),
            ])
            if pending >= MAX_PENDING_PER_MAILBOX:
                _logger.warning(
                    "[Incoming Mail] Triage queue for %s is full (%s pending); "
                    "not recording further skips", mailbox.email, pending,
                )
                return False

            message_id = message.get('message_id')
            if message_id:
                existing = self.sudo().search([
                    ('mailbox_id', '=', mailbox.id),
                    ('message_id', '=', message_id),
                ], limit=1)
                if existing:
                    return existing

            return self.sudo().create({
                'provider_message_id': message.get('provider_message_id'),
                'message_id': message_id,
                'mailbox_id': mailbox.id,
                'folder': folder,
                'direction': direction,
                'email_from': (message.get('from') or {}).get('email'),
                'email_to': ', '.join(
                    r.get('email') for r in (message.get('to') or []) if r.get('email')
                ),
                'subject': message.get('subject'),
                'date': message.get('date'),
                'partner_id': partner.id if partner else False,
                'reason': reason,
                'reason_detail': detail,
            })
        except Exception:
            _logger.exception(
                "[Incoming Mail] Could not record triage item for %s",
                message.get('provider_message_id'),
            )
            return False

    # -- actions ----------------------------------------------------------- #

    def action_import(self):
        """Re-run the message through the pipeline, bypassing the filters.

        Re-enters `_process_message()` rather than reimplementing routing, so an
        imported item goes through exactly the same threading, alias routing and
        `message_new()` path as any other mail. The force flag lifts the filter
        checks only — never the duplicate guard and never the Odoo loop guard.

        Which is exactly why its answer has to be read rather than assumed. The
        guards the flag does *not* lift — a duplicate, Odoo's own outgoing mail,
        a blocked contact, a sender who is an internal user — all return False,
        and writing `imported` anyway produced the worst row in the queue: gone
        from the pending filter, no message behind it, and a status claiming it
        had worked.

        A refusal is not automatically a failure, though. The duplicate guard
        fires precisely when the mail *is* already in Odoo, having arrived by
        another route since it was queued — the item's job is done and linking
        it to that message is the honest outcome. So the message is looked up
        either way, and only an item with nothing behind it stays pending.
        """
        processor = self.env['pan.mail.fetcher']
        Message = self.env['mail.message']
        imported = refused = 0
        for item in self:
            if item.state != 'pending':
                continue
            mailbox = item.mailbox_id
            client = mailbox._get_client()
            account = client.resolve_receiving_account(mailbox)
            preview = client.get_message(
                account=account, mailbox=mailbox,
                provider_message_id=item.provider_message_id,
            )
            processed = processor.with_context(
                pan_mail_force_import=True)._process_message(
                    mailbox, preview, item.folder or 'inbox')

            message = Message.search([('message_id', '=', item.message_id)], limit=1)
            if not processed and not message:
                _logger.info(
                    '[Mail Item] Import refused for %s; a guard the force flag '
                    'does not lift still applies', item.provider_message_id)
                refused += 1
                continue
            item.write({'state': 'imported', 'mail_message_id': message.id or False})
            imported += 1

        if refused:
            return self._notify_import_result(
                _('Not everything could be imported'),
                _('%(done)d imported, %(left)d left in the queue. Importing '
                  'cannot override a blocked contact, an internal sender, or '
                  "Odoo's own outgoing mail.",
                  done=imported, left=refused),
            )
        return True

    @staticmethod
    def _notify_import_result(title, message):
        """Sticky warning: the operator has rows left to deal with."""
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title, 'message': message,
                'type': 'warning', 'sticky': True,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def action_ignore(self):
        self.filtered(lambda i: i.state == 'pending').write({'state': 'ignored'})
        return True

    def action_open_message(self):
        self.ensure_one()
        if not self.mail_message_id:
            raise UserError(_('This item has not been imported.'))
        return self.mail_message_id.action_open_document()

    # -- AI enrichment ----------------------------------------------------- #

    def _compute_ai_suggested_name(self):
        for item in self:
            item.ai_suggested_name = False
            model, res_id = item.ai_suggested_model, item.ai_suggested_res_id
            if not model or not res_id or model not in self.env:
                continue
            record = self.env[model].browse(res_id).exists()
            if record:
                try:
                    item.ai_suggested_name = record.display_name
                except Exception:
                    item.ai_suggested_name = _('(no access)')

    def _build_candidates(self):
        """The shortlist the model is allowed to rank.

        Built by deterministic matching, never by the model. This is the whole
        safety property of the feature: the worst outcome is a badly ranked
        shortlist, not mail filed against a record nobody connected it to.
        """
        self.ensure_one()
        candidates = []
        partner = self.partner_id
        if partner:
            candidates.append({
                'model': 'res.partner', 'id': partner.id,
                'name': partner.display_name,
                'why': 'sender is this contact',
            })
            recent = self.env['mail.message'].sudo().search([
                ('author_id', '=', partner.id),
                ('model', '!=', False),
                ('model', '!=', 'res.partner'),
                ('res_id', '!=', False),
                ('message_type', '=', 'email'),
            ], order='date desc', limit=20)
            seen = set()
            for message in recent:
                key = (message.model, message.res_id)
                if key in seen or message.model not in self.env:
                    continue
                seen.add(key)
                record = self.env[message.model].browse(message.res_id).exists()
                if not record:
                    continue
                candidates.append({
                    'model': message.model, 'id': message.res_id,
                    'name': record.display_name,
                    'why': 'this contact has recent email on it',
                })
                if len(candidates) >= 6:
                    break
        return candidates

    def _ai_payload(self):
        self.ensure_one()
        return {
            'subject': self.subject,
            'from': self.email_from,
            'to': self.email_to,
            'date': str(self.date or ''),
            'candidates': self._build_candidates(),
        }

    @api.model
    def _cron_ai_classify(self, limit=20):
        """Enrich pending items with a routing suggestion.

        A separate cron from the fetcher, on purpose and structurally: nothing
        here can slow mail ingestion or roll a message back, because ingestion
        already finished before these records existed. Each item gets its own
        savepoint so one bad response cannot cost the batch.
        """
        backend = get_ai_backend(self.env)
        if not backend.is_available():
            return 0

        items = self.sudo().search([
            ('state', '=', 'pending'), ('ai_state', '=', 'todo'),
        ], limit=limit)

        enriched = 0
        for item in items:
            try:
                with self.env.cr.savepoint():
                    suggestion = backend.classify(item._ai_payload())
                    item.write(item._ai_values(suggestion))
                    enriched += 1
            except Exception:
                _logger.exception('[Mail AI] Could not classify item %s', item.id)
                try:
                    item.write({
                        'ai_state': 'failed',
                        'ai_attempts': item.ai_attempts + 1,
                    })
                except Exception:
                    _logger.exception('[Mail AI] Could not record AI failure')
        return enriched

    def _ai_values(self, suggestion):
        self.ensure_one()
        values = {
            'ai_attempts': self.ai_attempts + 1,
            'ai_prompt_version': PROMPT_VERSION,
            'ai_backend': self.env['ir.config_parameter'].sudo().get_param(
                'pan_mail_pro.ai_backend', 'none'),
        }
        if not suggestion:
            # "No opinion" is a real answer and a terminal one. Retrying it
            # would spend money to be told the same thing.
            values['ai_state'] = 'skipped'
            return values
        values.update({
            'ai_state': 'done',
            'ai_model': suggestion.get('backend_model'),
            'ai_confidence': suggestion.get('confidence') or 0.0,
            'ai_suggested_model': suggestion.get('suggested_model'),
            'ai_suggested_res_id': suggestion.get('suggested_res_id'),
            'ai_rationale': suggestion.get('rationale'),
        })
        return values

    def action_open_suggestion(self):
        """Open the record the AI proposed, so a human can judge it."""
        self.ensure_one()
        if not self.ai_suggested_model or not self.ai_suggested_res_id:
            raise UserError(_('There is no suggestion for this item.'))
        return {
            'type': 'ir.actions.act_window',
            'res_model': self.ai_suggested_model,
            'res_id': self.ai_suggested_res_id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }

    # -- housekeeping ------------------------------------------------------ #

    @api.model
    def _gc_items(self, limit=5000):
        """Delete expired items whatever their status.

        Retention is unconditional on purpose: an item is metadata about
        somebody's correspondence, and a queue nobody worked is not a reason to
        keep it. Bounded per run so the cron cannot hold a long transaction.
        """
        expired = self.sudo().search(
            [('expiry_date', '<=', fields.Datetime.now())], limit=limit)
        count = len(expired)
        if count:
            expired.unlink()
            _logger.info('[Incoming Mail] Removed %s expired triage item(s)', count)
        return count
