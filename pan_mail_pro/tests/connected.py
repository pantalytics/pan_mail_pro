# -*- coding: utf-8 -*-
"""Every test runs on a connected Odoo instance unless it asks for the real gate.

Mail Pro only syncs, and only connects a new account, on an instance linked to
a Pantalytics account (`pan.mail.license.sync_allowed`). Hundreds of tests
create accounts and run syncs and have nothing to say about that, so the
answer is yes for them. The tests of the gate itself put
`pan_mail_pro_real_gate` in their context and get the real check.

Imported first by `tests/__init__.py`, which Odoo only loads when running
tests; production code carries no test switch.
"""
from odoo import api

from odoo.addons.pan_mail_pro.models.pan_mail_license import PanMailLicense

REAL_SYNC_ALLOWED = PanMailLicense.sync_allowed


@api.model
def _sync_allowed(self):
    if self.env.context.get('pan_mail_pro_real_gate'):
        return REAL_SYNC_ALLOWED(self)
    return True


PanMailLicense.sync_allowed = _sync_allowed
