# -*- coding: utf-8 -*-
"""One pass of the sync over one mailbox: what it read and where it went.

The mailbox's Sync page lists these as Runs, Connector Pro's word and shape
(docs: the Mail Pro canvas, screens 08 and 09). A run is written by whoever
does the pass -- the fetch cron, Run Now, the hourly access check -- and
says how much it read, how much it linked to a record in Odoo, how much the
gates left out and how many messages failed. The mails themselves are the
routing log rows that carry its `run_id`: a run adds no copy of a subject,
an address or a body of its own.

A run that read nothing is the overwhelming majority (the cron reads every
mailbox every minute) and is worth a day, so the list shows the mailbox is
being read. A run that read something, or failed, is kept thirty days, the
same as the error ledger.
"""
import logging

from odoo import api, fields, models, _
from odoo.tools.sql import create_index

_logger = logging.getLogger(__name__)

# How long a run that read nothing and failed nothing is kept.
EMPTY_RUN_RETENTION_DAYS = 1
# How long any other run is kept: the error ledger's thirty days.
RUN_RETENTION_DAYS = 30

# The context key the fetch carries its run on, so the routing log and the
# counters find it without every method in the chain taking an argument.
RUN_CTX = 'pan_mail_sync_run_id'

# The states a run of each kind counts as a check in: the mailbox's
# `last_check_date` (incoming) and `access_checked_date` (access check) read
# the newest such run, and the vacuum keeps it. A failed read is not a check;
# a check the provider refused still waits its hour.
CHECKED_STATES = {
    'incoming': ('done',),
    'access_check': ('done', 'failed'),
}


class PanMailSyncRun(models.Model):
    _name = 'pan.mail.sync.run'
    _description = 'Mail Pro Sync Run'
    _order = 'started desc, id desc'
    _rec_name = 'started'

    mailbox_id = fields.Many2one(
        'pan.mail.mailbox', string='Mailbox', required=True,
        ondelete='cascade', index=True)
    kind = fields.Selection([
        ('incoming', 'Read Mail'),
        ('access_check', 'Access Check'),
    ], string='Run', required=True, default='incoming')
    started = fields.Datetime(string='Started', required=True,
                              default=fields.Datetime.now, index=True)
    took = fields.Float(string='Took (s)', digits=(10, 1))
    # Empty when the sync started it on its own.
    started_by = fields.Many2one('res.users', string='Started By')
    read_count = fields.Integer(string='Read')
    linked_count = fields.Integer(string='Linked')
    skipped_count = fields.Integer(string='Left Out')
    error_count = fields.Integer(string='Errors')
    state = fields.Selection([
        ('running', 'Running'),
        ('done', 'Done'),
        ('failed', 'Failed'),
    ], string='Status', required=True, default='running')
    message = fields.Char(string='Problem', help='Why the run failed, first line.')
    log_ids = fields.One2many('pan.mail.routing.log', 'run_id', string='Mails')

    @api.model
    def _start(self, mailbox, kind='incoming'):
        """A run of `kind` over `mailbox`, started now."""
        by = self.env.user if not self.env.context.get('ir_cron_progress_id') \
            and not self.env.su else self.env['res.users']
        return self.sudo().create({
            'mailbox_id': mailbox.id,
            'kind': kind,
            'started_by': by.id or False,
        })

    def _note_read(self, count):
        """`count` more messages listed by the provider in this run."""
        if self and count:
            self.sudo().write({'read_count': self.read_count + count})

    def _note_error(self):
        """One message of this run failed to process."""
        if self:
            self.sudo().write({'error_count': self.error_count + 1})

    def _finish(self, error=None):
        """Close the run: how long it took, how much it linked, and whether
        it failed. `error` is the reason, as the mailbox records it."""
        for run in self.sudo():
            linked = len(run.log_ids)
            took = (fields.Datetime.now() - run.started).total_seconds()
            vals = {
                'took': max(took, 0.0),
                'linked_count': linked,
                'skipped_count': max(run.read_count - linked - run.error_count, 0),
                'state': 'failed' if error else 'done',
            }
            if error:
                vals['message'] = str(error).splitlines()[0][:256] if str(error) else \
                    type(error).__name__
            run.write(vals)

    def init(self):
        # What the mailbox's two dates and the vacuum ask: the newest run of
        # one kind for one mailbox.
        create_index(self.env.cr, 'pan_mail_sync_run_mailbox_kind_started_idx',
                     self._table, ['mailbox_id', 'kind', 'started DESC', 'id DESC'])

    @api.model
    def _last_checks(self, mailbox_ids=None):
        """{(mailbox_id, kind): (id, started)} of the newest run of each kind
        that counts as a check (`CHECKED_STATES`), for `mailbox_ids` or all."""
        self.flush_model(['mailbox_id', 'kind', 'state', 'started'])
        where = ' OR '.join(
            f"(kind = '{kind}' AND state IN %s)" for kind in CHECKED_STATES)
        params = [tuple(states) for states in CHECKED_STATES.values()]
        scope = ''
        if mailbox_ids is not None:
            if not mailbox_ids:
                return {}
            scope = 'mailbox_id IN %s AND '
            params.insert(0, tuple(mailbox_ids))
        self.env.cr.execute(f"""
            SELECT DISTINCT ON (mailbox_id, kind) mailbox_id, kind, id, started
              FROM pan_mail_sync_run
             WHERE {scope}({where})
          ORDER BY mailbox_id, kind, started DESC, id DESC
        """, params)
        return {(mailbox, kind): (run, started)
                for mailbox, kind, run, started in self.env.cr.fetchall()}

    @api.autovacuum
    def _gc_runs(self):
        """A day for a run that read nothing, thirty for every other one.

        Except the runs the mailbox reads its dates from, whatever their age:
        the newest finished incoming run (`last_check_date`) and the newest
        finished access check (`access_checked_date`). A mailbox whose cron
        died a week ago has to keep saying when it was last read.
        """
        now = fields.Datetime.now()
        empty = fields.Datetime.subtract(now, days=EMPTY_RUN_RETENTION_DAYS)
        old = fields.Datetime.subtract(now, days=RUN_RETENTION_DAYS)
        keep = [run for run, _started in self._last_checks().values()]
        self.sudo().search([
            ('id', 'not in', keep),
            '|',
            ('started', '<', old),
            '&', '&', '&', ('started', '<', empty), ('state', '=', 'done'),
            ('read_count', '=', 0), ('error_count', '=', 0),
        ]).unlink()

    def action_open_mails(self):
        """The routing log rows of this run."""
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'pan_mail_pro.action_pan_mail_routing_log')
        action['domain'] = [('run_id', '=', self.id)]
        action['context'] = {}
        return action

    def _compute_display_name(self):
        kinds = dict(self._fields['kind']._description_selection(self.env))
        for run in self:
            when = fields.Datetime.context_timestamp(run, run.started) if run.started else None
            run.display_name = '%s, %s' % (
                kinds.get(run.kind, ''),
                when.strftime('%d %b %H:%M') if when else _('not started'))
