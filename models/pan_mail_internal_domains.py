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

# Domains that belong to a mail provider rather than to a company.
#
# A colleague whose Odoo login is a personal address would otherwise put one of
# these on the internal list, and the consequence of that is not a warning: an
# internal domain stops mail being synced, so marking gmail.com internal
# silently stops logging every customer who happens to use Gmail. The false
# positive is worse than the miss it prevents.
#
# The case being dropped: a company that genuinely runs on a public domain --
# a two-person shop whose only address is a gmail.com one -- gets no help from
# the suggestion and must type its domain in. Rare, loud when it happens, and
# recoverable in one field. The reverse error is silent and affects customers.
PUBLIC_MAIL_DOMAINS = frozenset({
    'aol.com', 'gmail.com', 'googlemail.com', 'gmx.com', 'gmx.de', 'gmx.net',
    'hotmail.co.uk', 'hotmail.com', 'icloud.com', 'live.com', 'live.nl',
    'mac.com', 'me.com', 'msn.com', 'outlook.com', 'pm.me', 'proton.me',
    'protonmail.com', 'yahoo.co.uk', 'yahoo.com', 'ymail.com', 'zoho.com',
})


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
        """Why Mail Pro must not be used yet, or None when it may.

        Returned rather than raised so each caller can pick its own exception
        type (ValidationError when saving a mailbox, UserError during a sync).

        This gates the first mailbox, not just incoming sync. Creating a mailbox
        is the moment Mail Pro takes over the company's mail, and a database
        that reaches that moment without knowing which domains are its own
        cannot tell a colleague from a customer. Not at install, though: an
        empty database has no domains to derive and nobody to protect, and the
        SMTP takeover waits for the same moment for the same reason.
        """
        if self.is_configured() or self.sync_internal_enabled():
            return None
        return _(
            'No internal email domains are configured. Mail Pro cannot be used '
            'yet, because without them it cannot tell your colleagues from your '
            'customers — and every internal email, confidential ones included, '
            'would be copied into Odoo.\n\n'
            'Go to Settings → Mail Pro → Internal Domains and enter your company '
            'domains, or explicitly enable "Sync internal email" if that is what '
            'you want.'
        )

    @api.model
    def completeness_error(self):
        """Which of our own domains the list is missing, or None when none.

        Separate from `configuration_error()` because they fail differently:
        one says the list is absent, this one says the list is wrong. The
        second is the more dangerous of the two, because a database with a
        half-filled list passes every check that asks whether it is configured.
        """
        missing = self.uncovered_domains()
        if not missing:
            return None
        return _(
            'These domains belong to your company but are not in the internal '
            'domain list: %s\n\n'
            'Mail sent to or from them would be treated as customer '
            'correspondence and copied into Odoo. Add them, or use "Apply '
            'suggested" to fill the list from the addresses already in this '
            'database.'
        ) % ', '.join(missing)

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
    def _own_addresses(self):
        """Every address this database can demonstrate belongs to the company.

        Two sources, and the second is the one that matters. A configured
        mailbox is ours by definition. So is an internal user's own address --
        and that is the source that catches a second domain, because a company
        that acquired another one has colleagues on it long before it has
        mailboxes on it. Juffermans Machinebouw runs every mailbox on one
        domain and has a colleague on a second; a list built from mailboxes
        alone reads as complete and treats that colleague as an outsider.

        Portal and public users are excluded: a customer with a login is not
        the company, and folding their domain in would mark a customer's mail
        internal and stop syncing it.
        """
        mailboxes = self.env['pan.mail.mailbox'].sudo().with_context(
            active_test=False
        ).search([])
        users = self.env['res.users'].sudo().with_context(
            active_test=False
        ).search([('share', '=', False)])
        addresses = [
            address for address in mailboxes.mapped('email') + users.mapped('email')
            if address
        ]
        return [
            address for address in addresses
            if self._parse(address) and self._parse(address)[0] not in PUBLIC_MAIL_DOMAINS
        ]

    @api.model
    def suggest_domains(self):
        """Domains we can derive from what is already in the database.

        The company's own addresses first (see `_own_addresses`), then the
        companies' own email addresses and Odoo's alias domains.
        """
        candidates = list(self._own_addresses())

        companies = self.env['res.company'].sudo().search([])
        candidates += [c.email for c in companies if c.email]

        alias_domains = self.env['mail.alias.domain'].sudo().search([])
        candidates += [d.name for d in alias_domains if d.name]

        return self._parse(', '.join(filter(None, candidates)))

    @api.model
    def uncovered_domains(self):
        """Our own domains that the configured list does not carry.

        A configured list is not the same as a complete one, and only the
        second is worth anything: a domain we demonstrably own that is missing
        from the list has all of its internal mail treated as correspondence
        and synced. That is the original leak exactly, on a database that
        passes every "is it configured" check.
        """
        if not self.is_configured():
            return []
        configured = set(self.get_domains())
        own = self._parse(', '.join(self._own_addresses()))
        return [d for d in own if d not in configured]
