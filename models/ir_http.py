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
"""
from odoo import models


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    def session_info(self):
        result = super().session_info()
        if self.env.user._is_internal():
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
