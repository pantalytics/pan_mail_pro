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

The second thing here is Email Marketing's road (#445). Mass mailings leave
through Odoo's own SMTP on purpose, but the takeover leaves only its
placeholder active, and the placeholder (sequence 0, no from filter) wins
Odoo's server choice over a Brevo server added later. So a campaign went to
`invalid.mail-pro.disabled`. Under `MARKETING` the placeholder is not a
candidate, and Odoo chooses among the real servers exactly as it would
without Mail Pro. The takeover, for its part, leaves the server Email
Marketing names as its Dedicated Server switched on (`_marketing_server`).
"""
from odoo import api, models

from ..compat import MAIL_SERVER_BUILD

from .mail_mail import MARKETING, ON_COPY
from .neutralization import COPY_FROM_HEADER
from .pan_mail_mailbox import SMTP_PLACEHOLDER_XMLID


class IrMailServer(models.Model):
    _inherit = 'ir.mail_server'

    def _copy_from(self, email_from, kwargs):
        headers = kwargs.get('headers') or {}
        if self.env.context.get(ON_COPY) and headers.get(COPY_FROM_HEADER):
            return headers[COPY_FROM_HEADER]
        return email_from

    # The builder is `_build_email__` on 19 and 20, `build_email` on 18
    # (docs/plans/multi-version.md); the override wears the running name.
    if MAIL_SERVER_BUILD == '_build_email__':
        def _build_email__(self, email_from, *args, **kwargs):  # noqa: PLW3201 - Odoo's own name
            return super()._build_email__(self._copy_from(email_from, kwargs), *args, **kwargs)
    else:
        def build_email(self, email_from, *args, **kwargs):
            return super().build_email(self._copy_from(email_from, kwargs), *args, **kwargs)

    def _find_mail_server(self, email_from, mail_servers=None):
        """Odoo's server choice, minus the placeholder for a campaign.

        On 18, 19 and 20 alike this is where `mail.mail` picks a server for a
        mail that names none. With no real server left, Odoo falls through to
        the one in odoo.conf, which is what it does without Mail Pro too.
        """
        placeholder = self.env.context.get(MARKETING) and self.env.ref(
            SMTP_PLACEHOLDER_XMLID, raise_if_not_found=False)
        if placeholder:
            if mail_servers is None:
                mail_servers = self.sudo().search([('id', '!=', placeholder.id)], order='sequence')
            else:
                mail_servers -= placeholder
        return super()._find_mail_server(email_from, mail_servers=mail_servers)

    @api.model
    def _marketing_server(self):
        """The server Email Marketing names as its Dedicated Server, or none.

        The one server the takeover may not switch off: someone chose it for
        campaigns, and no mail but a campaign is ever sent through it. Read
        off `mass_mailing.mail_server_id` alone, the parameter Odoo's own
        archive guard reads: it refuses to archive that server, so the
        takeover used to fail the first mailbox's create on it.
        """
        server_id = self.env['ir.config_parameter'].sudo().get_param('mass_mailing.mail_server_id')
        if not str(server_id).isdigit():
            return self.browse()
        return self.sudo().with_context(active_test=False).browse(int(server_id)).exists()
