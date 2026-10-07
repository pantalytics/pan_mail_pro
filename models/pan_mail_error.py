# -*- coding: utf-8 -*-
"""
What went wrong, where, and how often.

Every failure this module catches used to be one line in the server log. On
Cloudpepper and odoo.sh that log is a file nobody opens, rotated away in days,
and it is the one place a customer cannot look while telling us "mail stopped".
So a failure that is caught is also *recorded*: one row here with a short
code, the flow it happened in, the mailbox or account it concerned, the
exception and its traceback. The log line stays; this is the copy that
survives the week and can be read from a screen (Settings → Technical →
Email → Mail Pro → Errors).

**The code is the only part that leaves the database.** `codes_since()` groups
the last 24 hours by code and the heartbeat carries that list as `errors`
(`pan_mail_license._heartbeat_body`); the server turns each code into a
`heartbeat_error` event in PostHog, so "which release broke sending for whom"
is a chart rather than a support ticket. A code is a fixed string from `CODES`
below: no address, subject, body, exception text or traceback ever rides along.
A code that has not been seen in the last day is reported within the minute
(`pan_mail_license._report_errors_if_new`), because during a beta a new kind of
failure is the thing to hear about today, not tomorrow.

**Written on a cursor of its own.** The failures worth recording are the ones
whose transaction is about to be rolled back: the cron that died, the request
that 500ed. A row written in that transaction dies with it, so `record()` opens
its own cursor and commits, the way Odoo's own `ir.logging` handler does, and
never raises: a failure to record a failure is a log line, not a second
failure. The Many2one columns point at rows the failing transaction may have
*updated* (a mailbox's `error_message`, an account's cleared tokens); that is
fine, a foreign key check waits only on a row the other transaction is
deleting or re-keying, and nothing records an error about a row it is deleting.

**Thirty days, then gone.** Odoo's autovacuum deletes older rows; the value of
a failure row is in the days after it is written, and the heartbeat has long
since carried its code.

In the test suite `tests/ledger.py` routes `record()` into the test's own
transaction (a repeatable-read transaction cannot see a row committed beside
it); `tests/test_errors.py` exercises the real cursor under registry test mode.
"""
import logging
import traceback as traceback_module

from odoo import SUPERUSER_ID, api, fields, models

from .mail_provider_client import PROVIDER_SELECTION

_logger = logging.getLogger(__name__)

RETENTION_DAYS = 30

# The server accepts twenty codes of forty characters; the list below is the
# whole vocabulary, so the caps only guard against a typo.
MAX_CODE_LENGTH = 40
MAX_CODES_REPORTED = 20
MAX_MESSAGE_LENGTH = 500

FLOWS = [
    ('incoming', 'Incoming'),
    ('outgoing', 'Outgoing'),
    ('oauth', 'Connecting a mailbox'),
    ('access', 'Mailbox access'),
    ('license', 'Pantalytics'),
    ('inbox', 'Inbox'),
]

# `<flow>.<what>`: the fixed vocabulary. Adding one is adding a line here and
# a call site; the test on this file refuses a code that is not in the list.
CODES = {
    'incoming.mailbox_failed': 'A sync run of a mailbox raised',
    'incoming.message_failed': 'One message could not be processed; the cursor stalls on it',
    'incoming.throttled': 'The provider asked the sync to wait',
    'incoming.rule_failed': 'A matching rule raised and was skipped; the mail may have landed lower',
    'incoming.index_failed': 'The Message-ID or thread index could not be written; later replies may not thread',
    'incoming.attachments_failed': 'The attachments of a message could not be fetched; the mail was imported without them',
    'outgoing.no_route': 'No mailbox could send this mail',
    'outgoing.send_failed': 'The provider refused the send or the send raised',
    'outgoing.throttled': 'The provider asked the send to wait',
    'outgoing.sent_copy_failed': 'The mail went out but its copy could not be filed in the Sent folder',
    'oauth.callback_failed': 'The consent round came back and could not be stored',
    'oauth.token_revoked': 'A refresh token was refused for good; the account must reconnect',
    'oauth.tenant_mismatch': 'A sign-in from a directory the registration cannot serve was refused',
    'access.read_denied': 'A sign-in cannot read a mailbox it is expected to read',
    'access.send_denied': 'The provider refused a send as the address: a right is missing',
    'access.no_mailbox': 'There is no mailbox at a configured address',
    'access.check_failed': 'The access check itself raised, before the provider could answer',
    'license.heartbeat_failed': 'The heartbeat to Pantalytics raised or was refused',
    'inbox.rpc_failed': 'A request from the browser failed inside this module',
}


class PanMailError(models.Model):
    _name = 'pan.mail.error'
    _description = 'Mail Pro Error'
    _order = 'id desc'
    _rec_name = 'code'

    code = fields.Char(required=True, index=True, size=MAX_CODE_LENGTH)
    flow = fields.Selection(FLOWS, required=True, index=True)
    level = fields.Selection(
        [('warning', 'Warning'), ('error', 'Error')], required=True, default='error',
        help='A warning is a failure the module recovers from on its own, such as a '
             'provider asking it to wait. An error needs a person, or a fix.')
    provider = fields.Selection(PROVIDER_SELECTION)
    mailbox_id = fields.Many2one('pan.mail.mailbox', ondelete='set null', index=True)
    account_id = fields.Many2one('pan.mail.account', ondelete='set null')
    user_id = fields.Many2one('res.users', ondelete='set null',
                              help='Who was acting when it failed; empty for the cron.')
    message = fields.Char(size=MAX_MESSAGE_LENGTH, help='The exception, first line.')
    traceback = fields.Text()
    description = fields.Char(compute='_compute_description')

    @api.depends('code')
    def _compute_description(self):
        for row in self:
            row.description = CODES.get(row.code, '')

    # ------------------------------------------------------------------ #
    # Writing
    # ------------------------------------------------------------------ #

    @api.model
    def _record(self, code, error=None, *, level='error', mailbox=None, account=None,
               detail=None):
        """One row for one failure, committed on its own, never raising.

        `error` is the exception when there is one; `detail` is the sentence
        when there is not (a provider that answered `success: False` with a
        text). Either lands in `message`, the traceback in `traceback`.
        """
        if code not in CODES:
            _logger.error('[Mail Pro] Unknown error code %r; recorded as is', code)
        try:
            vals = self._values(code, error, level, mailbox, account, detail)
            with self.pool.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                env['pan.mail.error'].create(vals)
        except Exception:  # noqa: BLE001 - a failure to record a failure is a log line
            _logger.exception('[Mail Pro] Could not record error %s', code)

    def _values(self, code, error, level, mailbox, account, detail):
        message = detail or ''
        trace = ''
        if error is not None:
            message = str(error).splitlines()[0] if str(error) else type(error).__name__
            message = f'{type(error).__name__}: {message}'
            if error.__traceback__ is not None:
                trace = ''.join(traceback_module.format_exception(
                    type(error), error, error.__traceback__))
        provider = (mailbox and mailbox.provider) or (account and account.provider) or False
        uid = self.env.uid
        return {
            'code': code[:MAX_CODE_LENGTH],
            'flow': code.split('.', 1)[0] if code.split('.', 1)[0] in dict(FLOWS) else 'inbox',
            'level': level,
            'provider': provider,
            'mailbox_id': mailbox.id if mailbox else False,
            'account_id': account.id if account else False,
            'user_id': uid if uid and uid != SUPERUSER_ID else False,
            'message': message[:MAX_MESSAGE_LENGTH],
            'traceback': trace or False,
        }

    # ------------------------------------------------------------------ #
    # Reading: what the heartbeat carries
    # ------------------------------------------------------------------ #

    @api.model
    def codes_since(self, since):
        """The last day's failures as `[{'code', 'count'}]`, most frequent
        first, capped at what the server accepts. Codes only."""
        groups = self.sudo()._read_group(
            [('create_date', '>=', since)], groupby=['code'], aggregates=['__count'])
        rows = sorted(groups, key=lambda group: (-group[1], group[0]))
        return [{'code': code, 'count': count} for code, count in rows[:MAX_CODES_REPORTED]]

    @api.model
    def signature_since(self, since):
        """The set of codes seen since `since`, as one string to compare two
        heartbeats by. A code appearing, or ageing out, changes it."""
        return ','.join(sorted(row['code'] for row in self.codes_since(since)))

    # ------------------------------------------------------------------ #
    # Housekeeping
    # ------------------------------------------------------------------ #

    @api.autovacuum
    def _gc_errors(self):
        cutoff = fields.Datetime.subtract(fields.Datetime.now(), days=RETENTION_DAYS)
        stale = self.sudo().search([('create_date', '<', cutoff)])
        if stale:
            _logger.info('[Mail Pro] Removing %s error row(s) older than %s days',
                         len(stale), RETENTION_DAYS)
            stale.unlink()
