# -*- coding: utf-8 -*-
"""Four things in the session: does this user still have to connect a
mailbox, may they open the Inbox at all, is this Odoo connected to a
Pantalytics account, and may the Inbox report how it is used.

The webclient asks nothing extra for it. `session_info` is already fetched once
per page load, so the banner knows whether to draw itself before the first
paint -- an RPC of its own would show every user a banner that appears a moment
after the screen has settled, which is how a nudge turns into a flicker.

Stale by one page load, on purpose: the answer only changes when somebody
finishes a consent round, and that round comes back through
`/microsoft_oauth/callback`, which is a full page load.

Every page load runs this, so a failure here is a 500 on every screen. That is
what a host gets when it pulls new code without running `-u pan_mail_pro`: the
flags read a table or column the upgrade has not made yet. The flags then fall
back to "no" and the log says to upgrade, so Odoo itself keeps working and only
Mail Pro waits for the upgrade.
"""
import logging
import traceback

from psycopg2 import errors
from werkzeug.exceptions import HTTPException

from odoo import models
from odoo.exceptions import AccessDenied, UserError
from odoo.http import request

_logger = logging.getLogger(__name__)

SCHEMA_BEHIND = (errors.UndefinedTable, errors.UndefinedColumn)

# What a request may end in on purpose: a refusal, a validation message, a
# redirect. None of them is a defect, and recording them would bury the ones
# that are under every "not connected" the Inbox answers.
EXPECTED = (UserError, AccessDenied, HTTPException)


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    def session_info(self):
        result = super().session_info()
        if self.env.user._is_internal():
            flags = dict.fromkeys((
                'pan_mail_connect_prompt', 'pan_mail_improve',
                'pan_mail_inbox', 'pan_mail_connected'), False)
            try:
                with self.env.cr.savepoint():
                    flags.update(self._pan_mail_session_flags())
            except SCHEMA_BEHIND as exc:
                _logger.warning(
                    "Mail Pro: the database is behind the code (%s). "
                    "Run an upgrade of pan_mail_pro (-u pan_mail_pro); until "
                    "then Mail Pro reports itself as not connected.",
                    exc.pgerror.splitlines()[0] if exc.pgerror else exc)
                self.env.invalidate_all()
            result.update(flags)
        return result

    @classmethod
    def _handle_error(cls, exception):
        """A request that failed inside this module leaves a row in
        `pan.mail.error` (code `inbox.rpc_failed`) before Odoo answers it.

        The Inbox is RPC methods on `pan.mail.conversation` and the composer;
        an exception there used to reach the reader as a dialog and us never,
        because the request's transaction rolls back and the server log is
        the customer's. Only exceptions whose traceback passes through this
        module, and never the expected ones (`EXPECTED`).
        """
        cls._pan_mail_record_failure(exception)
        return super()._handle_error(exception)

    @classmethod
    def _pan_mail_record_failure(cls, exception):
        try:
            if isinstance(exception, EXPECTED) or not request:
                return
            frames = traceback.extract_tb(exception.__traceback__)
            if not any('pan_mail_pro' in frame.filename for frame in frames):
                return
            request.env['pan.mail.error']._record('inbox.rpc_failed', exception)
        except Exception:  # noqa: BLE001 - never a second failure on top of the first
            _logger.exception('[Mail Pro] Could not record the failed request')

    def _pan_mail_session_flags(self):
        result = {}
        result['pan_mail_connect_prompt'] = \
            self.env.user._pan_mail_should_prompt_connect()
        # Help improve Mail Pro, for the same reason and with the same
        # staleness: the answer changes once a day at most, and the Inbox
        # reads it before its first paint (pan_mail_license.improve_config).
        result['pan_mail_improve'] = self.env['pan.mail.license'].improve_config()
        # Door 1's button, in every chatter. Asked here rather than over
        # RPC per record: without it the chatter would call the read
        # layer on every form a plain user opens, and be refused every
        # time -- an AccessError per record open, in everybody's log.
        result['pan_mail_inbox'] = self.env.user.has_group(
            'pan_mail_pro.group_mail_mailbox_manager')
        # Whether the Inbox opens at all: Mail Pro works on a connected
        # Odoo, and a screen full of panes on an instance that is not is
        # a product that looks finished and is not. Stale by a page load
        # like the rest; the Inbox asks once more when this says no, so
        # an admin who has just connected is not shown the gate again.
        result['pan_mail_connected'] = self.env['pan.mail.license'].sync_allowed()
        return result
