# -*- coding: utf-8 -*-
"""One flag in the session: does this user still have to connect a mailbox?

The webclient asks nothing extra for it. `session_info` is already fetched once
per page load, so the banner knows whether to draw itself before the first
paint -- an RPC of its own would show every user a banner that appears a moment
after the screen has settled, which is how a nudge turns into a flicker.

Stale by one page load, on purpose: the answer only changes when somebody
finishes a consent round, and that round comes back through
`/microsoft_oauth/callback`, which is a full page load.
"""
from odoo import models


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    def session_info(self):
        result = super().session_info()
        if self.env.user._is_internal():
            result['pan_mail_connect_prompt'] = \
                self.env.user._pan_mail_should_prompt_connect()
        return result
