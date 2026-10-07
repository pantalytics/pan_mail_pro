# -*- coding: utf-8 -*-
"""What one sign-in may do with one mailbox, as the provider last said.

One row per (mailbox, account). Two answers on it, `can_read` and
`can_send`, each `yes`, `no` or `unknown`, and the provider's own sentence
when it refused. The rows are written by two things and nothing else: the
access check (`pan.mail.mailbox._verify_access`, which asks the provider)
and the send path (`mail.mail`, which reports what a real send met). Every
screen that says whether a mailbox works -- the health badge, the sentence
under it, step 4 of the setup checklist, the heartbeat -- reads this table
and asks the provider nothing itself.

Why a table and not two fields on the mailbox: on Microsoft 365 a shared
mailbox is sent from with each sender's own token, so "can send" is a
question per sender, and the one answer an administrator needs when a
quotation did not leave is *which* sign-in Exchange refused. On Gmail and
IMAP there is one sign-in per mailbox and the table holds one row; the
shape is the same, the count is not.

`unknown` is the honest default. Microsoft cannot say whether a sign-in may
send as an address without a send, and an SMTP server's 250 to MAIL FROM
proves nothing; both become `yes` on the first delivered mail and `no` on
the first refusal. Gmail answers before a send (`sendAs.list`), so there
the row is complete after the check alone.
"""
import logging

from odoo import api, fields, models

from .mail_provider_client import ACCESS_ANSWERS

_logger = logging.getLogger(__name__)


class PanMailMailboxAccess(models.Model):
    _name = 'pan.mail.mailbox.access'
    _description = 'Mailbox Access'
    _order = 'mailbox_id, account_id'

    mailbox_id = fields.Many2one(
        'pan.mail.mailbox', required=True, ondelete='cascade', index=True)
    account_id = fields.Many2one(
        'pan.mail.account', string='Sign-in', required=True, ondelete='cascade', index=True)
    can_read = fields.Selection(
        ACCESS_ANSWERS, string='Can read', required=True, default='unknown',
        help='Whether this sign-in can open the mailbox, as the provider last '
             'answered the access check.')
    can_send = fields.Selection(
        ACCESS_ANSWERS, string='Can send', required=True, default='unknown',
        help='Whether this sign-in may send as the address. Gmail answers it '
             'before a send; Microsoft 365 and SMTP only on one.')
    checked_date = fields.Datetime(
        string='Checked', readonly=True,
        help='When the access check last asked the provider about this pair.')
    sent_date = fields.Datetime(
        string='Last send', readonly=True,
        help='When a real send last moved the send answer, either way.')
    error = fields.Char(
        help="The provider's own reason for the last refusal, or empty.")

    _unique_pair = models.Constraint(
        'UNIQUE(mailbox_id, account_id)',
        'One row per mailbox and sign-in.',
    )

    @api.depends('mailbox_id.email', 'account_id.email')
    def _compute_display_name(self):
        for row in self:
            row.display_name = f'{row.account_id.email} on {row.mailbox_id.email}'

    # ------------------------------------------------------------------ #
    # Writing: two callers, both through here
    # ------------------------------------------------------------------ #

    @api.model
    def _row(self, mailbox, account):
        """The row for this pair, created on first sight. Sudo: the send path
        runs as whoever pressed Send, who has no business writing this table
        by hand and every business having it written on their behalf."""
        Access = self.sudo()
        row = Access.search([
            ('mailbox_id', '=', mailbox.id), ('account_id', '=', account.id)], limit=1)
        if not row:
            row = Access.create({'mailbox_id': mailbox.id, 'account_id': account.id})
        return row

    @api.model
    def note_check(self, mailbox, account, answer):
        """What the access check found for this pair (an `access_shape`).

        `can_read` is always the check's to write. `can_send` only where the
        check could answer it: an `unknown` from the check leaves what a
        real send last said, which is better evidence than no evidence.
        """
        row = self._row(mailbox, account)
        vals = {
            'can_read': answer['can_read'],
            'checked_date': fields.Datetime.now(),
            'error': answer.get('error') or False,
        }
        if answer['can_send'] != 'unknown':
            vals['can_send'] = answer['can_send']
        row.write(vals)
        return row

    @api.model
    def note_send(self, mailbox, account, delivered, error=None):
        """What a real send met: delivered, or refused on rights.

        Only those two. A send that failed for another reason (a bounce, a
        size limit, a throttle) says nothing about rights and leaves the
        answer as it was.
        """
        if not mailbox or not account:
            return self.browse()
        row = self._row(mailbox, account)
        vals = {'can_send': 'yes' if delivered else 'no',
                'sent_date': fields.Datetime.now()}
        if delivered:
            # A delivered mail also proves the sign-in reached the mailbox,
            # on the providers that draft inside it before sending.
            if row.can_read == 'unknown':
                vals['can_read'] = 'yes'
            if row.error:
                vals['error'] = False
        else:
            vals['error'] = error or False
        row.write(vals)
        return row

    # ------------------------------------------------------------------ #
    # Reading
    # ------------------------------------------------------------------ #

    @api.model
    def for_pair(self, mailbox, account):
        """The row for this pair, or an empty recordset. Read as sudo: the
        health of a mailbox is computed for whoever looks at it."""
        if not mailbox or not account:
            return self.sudo().browse()
        return self.sudo().search([
            ('mailbox_id', '=', mailbox.id), ('account_id', '=', account.id)], limit=1)

    def refused(self):
        """The rows where the provider said no to either question."""
        return self.filtered(lambda row: 'no' in (row.can_read, row.can_send))
