# -*- coding: utf-8 -*-
"""The error ledger writes in the test's own transaction, unless a test asks
for the real thing.

`pan.mail.error.record()` opens a cursor of its own and commits, so a failure
whose transaction rolls back still leaves its row. A `TransactionCase` cannot
live with that: its transaction is repeatable-read, so a row committed next to
it is invisible, the mailbox the row points at is not committed, so the insert
fails its foreign key, and whatever does commit stays in the developer's
database after the run. So here every `record()` lands in the caller's
transaction, and rolls back with the test like everything else.

The real path is still tested: `tests/test_errors.py` puts
`pan_mail_pro_real_ledger` in the context inside `enter_registry_test_mode()`,
where a new cursor is a savepoint on the test's own.

Imported second by `tests/__init__.py`, after `connected`, and like it only
when Odoo runs tests; production code carries no test switch.
"""
from odoo import api

from odoo.addons.pan_mail_pro.models.pan_mail_error import PanMailError

REAL_RECORD = PanMailError.record


@api.model
def _record(self, code, error=None, *, level='error', mailbox=None, account=None, detail=None):
    if self.env.context.get('pan_mail_pro_real_ledger'):
        return REAL_RECORD(self, code, error, level=level, mailbox=mailbox,
                           account=account, detail=detail)
    self.sudo().create(self._values(code, error, level, mailbox, account, detail))


PanMailError.record = _record
