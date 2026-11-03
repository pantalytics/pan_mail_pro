# -*- coding: utf-8 -*-
"""The From of a copy's outgoing mail.

On a neutralized database Mail Pro hands outgoing mail to Odoo's own mail
server so the host's catcher shows it (`mail.mail._send_on_staging`). Odoo
builds that message from the dicts `mail.mail._prepare_outgoing_list` returns,
except the From, which it reads off the record. On a notification the record's
From is the shared `mail.message` of the person who wrote it, and rewriting it
there would change what the chatter says for every other recipient of that
message. So the mailbox travels as a header, and the From is put in place here,
where the message is built.

Only inside `_send_on_staging` (the `ON_COPY` context key) and only on a
message that carries the header: every other mail Odoo builds is untouched.
"""
from odoo import models

from .mail_mail import ON_COPY
from .neutralization import COPY_FROM_HEADER


class IrMailServer(models.Model):
    _inherit = 'ir.mail_server'

    def _build_email__(self, email_from, *args, **kwargs):  # noqa: PLW3201 - Odoo's own name
        headers = kwargs.get('headers') or {}
        if self.env.context.get(ON_COPY) and headers.get(COPY_FROM_HEADER):
            email_from = headers[COPY_FROM_HEADER]
        return super()._build_email__(email_from, *args, **kwargs)
