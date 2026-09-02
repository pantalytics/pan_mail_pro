# -*- coding: utf-8 -*-
"""
Internal domain configuration — the fail-closed half of incoming sync.

Everything that has to answer "is this address one of ours?" asks this model, so
there is exactly one definition of an internal domain in the module.

Why this is not `mail.alias.domain`
-----------------------------------
That model is Odoo's *inbound* alias / catchall domain. It is auto-created on
install (usually `yourcompany.com`), it does not have to match the domains a
company actually sends mail from, and one alias domain can sit in front of
several sending domains. Reading it as "our domains" made the filter look
configured when it was not — and an empty list used to mean "nothing is
internal", so a database without alias domains synced every internal mail into
Odoo. That is a data leak, not a missing feature.

The rule now: incoming sync stays switched off until the domains are configured
or an admin explicitly opts out. Alias domains still feed `suggest_domains()`,
they just no longer decide anything by themselves.
"""
import logging
import re

from odoo import _, api, models

_logger = logging.getLogger(__name__)

# Config parameters live under the module's existing namespace.
PARAM_DOMAINS = 'pan_mail_pro.internal_domains'
PARAM_SYNC_INTERNAL = 'pan_mail_pro.sync_internal_email'

_SPLIT_RE = re.compile(r'[\s,;]+')


class PanMailInternalDomains(models.AbstractModel):
    _name = 'pan.mail.internal.domains'
    _description = 'Internal Email Domain Configuration'

    # -------------------------------------------------------------------------
    # Reading / writing the configuration
    # -------------------------------------------------------------------------
    @api.model
    def _parse(self, raw):
        """Turn free text into a clean, de-duplicated list of domains.

        Accepts what an admin actually types: commas, semicolons, newlines,
        `@domain.com`, `Domain.COM`, a full address pasted by accident.
        """
        domains = []
        for chunk in _SPLIT_RE.split(raw or ''):
            chunk = chunk.strip().lower().lstrip('@')
            if '@' in chunk:  # somebody pasted a whole address
                chunk = chunk.rsplit('@', 1)[1]
            chunk = chunk.strip('.')
            if chunk and '.' in chunk and chunk not in domains:
                domains.append(chunk)
        return domains

    @api.model
    def get_domains(self):
        """The configured internal domains, lowercase, no duplicates."""
        raw = self.env['ir.config_parameter'].sudo().get_param(PARAM_DOMAINS, '')
        return self._parse(raw)

    @api.model
    def set_domains(self, domains):
        self.env['ir.config_parameter'].sudo().set_param(
            PARAM_DOMAINS, ', '.join(self._parse(', '.join(domains)))
        )

    @api.model
    def sync_internal_enabled(self):
        """True when an admin deliberately turned the internal filter off."""
        return self.env['ir.config_parameter'].sudo().get_param(
            PARAM_SYNC_INTERNAL
        ) in ('True', 'true', '1')

    @api.model
    def is_configured(self):
        return bool(self.get_domains())

    # -------------------------------------------------------------------------
    # The gate
    # -------------------------------------------------------------------------
    @api.model
    def configuration_error(self):
        """Why incoming sync must not run, or None when it may.

        Returned rather than raised so each caller can pick its own exception
        type (ValidationError when saving a mailbox, UserError during a sync).
        """
        if self.is_configured() or self.sync_internal_enabled():
            return None
        return _(
            'No internal email domains are configured. Incoming sync is blocked '
            'because without them every internal email — including confidential '
            'ones — would be copied into Odoo.\n\n'
            'Go to Settings → Mail Pro → Internal Domains and enter your company '
            'domains, or explicitly enable "Sync internal email" if that is what '
            'you want.'
        )

    # -------------------------------------------------------------------------
    # The question everything else asks
    # -------------------------------------------------------------------------
    @api.model
    def is_internal(self, email):
        """Is this address one of our own domains?

        Purely about the domain list. Whether the caller should *act* on that is
        a separate question — see `should_skip()`.
        """
        if not email or '@' not in email:
            return False
        domain = email.rsplit('@', 1)[1].strip().lower().strip('.')
        return bool(domain) and domain in self.get_domains()

    @api.model
    def should_skip(self, email, mailbox=None):
        """Should this incoming message be skipped as internal?

        Three ways to answer "no": the global opt-out is on, this mailbox opted
        out (a team mailbox that wants internal forwards logged), or the address
        simply is not ours.
        """
        if self.sync_internal_enabled():
            return False
        if mailbox is not None and mailbox and not mailbox.exclude_internal:
            return False
        return self.is_internal(email)

    # -------------------------------------------------------------------------
    # Helping the admin fill it in
    # -------------------------------------------------------------------------
    @api.model
    def suggest_domains(self):
        """Domains we can derive from what is already in the database.

        Three sources, most trustworthy first: the addresses of configured
        mailboxes (those are demonstrably the company's own), the companies'
        own email addresses, and Odoo's alias domains.
        """
        candidates = []

        mailboxes = self.env['pan.mail.mailbox'].sudo().with_context(
            active_test=False
        ).search([])
        candidates += mailboxes.mapped('email')

        companies = self.env['res.company'].sudo().search([])
        candidates += [c.email for c in companies if c.email]

        alias_domains = self.env['mail.alias.domain'].sudo().search([])
        candidates += [d.name for d in alias_domains if d.name]

        return self._parse(', '.join(filter(None, candidates)))

    @api.model
    def uncovered_mailbox_domains(self):
        """Mailbox domains missing from the internal list.

        A mailbox we send from is by definition one of ours. If its domain is
        not in the list, internal mail from that domain is being synced — the
        exact shape of the original leak, so it is worth saying out loud even
        when the list is technically "configured".
        """
        if not self.is_configured():
            return []
        configured = set(self.get_domains())
        mailbox_domains = self._parse(', '.join(
            self.env['pan.mail.mailbox'].sudo().with_context(
                active_test=False
            ).search([]).mapped('email')
        ))
        return [d for d in mailbox_domains if d not in configured]
