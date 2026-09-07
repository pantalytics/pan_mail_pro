# -*- coding: utf-8 -*-
"""The provider this database sends and receives on.

This is the application credential — the Azure app registration or the Google
Cloud OAuth client — not a person's login. A person's login is a
`pan.mail.account`. One provider can serve many accounts; every account of one
provider shares this one registration, because that is what the provider's
console actually asks for: one app, many users consenting to it.

**There is one row.** It used to be a row per provider with an `in_use`
toggle naming the chosen one, so that switching kept the credentials of the
provider you switched away from. Nobody switches back and forth: a database
runs on one provider, and the toggle was a second question ("which of these
is it?") on a table that only ever had one meaningful answer. Creating a
second row is refused, so "which provider is this database set up for" is
`current()` — the row, if there is one.

Switching providers now means changing `provider` on that row, or deleting it
and adding the other. The old registration's client secret is gone at that
point; the provider's console can issue a new one, and that is cheaper than a
toggle every reader has to reason about.

IMAP has no application registration — a server, a login and a password are
per-address by nature, so its row carries no credential fields at all.
"Configured" for IMAP means "at least one account exists"; `credentials_set`
says so per provider, and `pan.mail.setup` never has to know the difference.

The secret is Fernet-encrypted at rest, like every other secret in the module,
and it is never handed back to the browser once saved: `client_secret` reads
back a placeholder, and re-saving the placeholder is a no-op rather than
encrypting the placeholder itself.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from . import encryption_utils
from .mail_provider_client import (
    DEFAULT_PROVIDER,
    OAUTH_CALLBACK_PATHS,
    PROVIDER_SELECTION,
    get_provider_client,
    oauth_redirect_uri,
)

SECRET_PLACEHOLDER = '********'


class PanMailProvider(models.Model):
    _name = 'pan.mail.provider'
    _description = 'Provider Registration'
    _order = 'provider'
    _rec_name = 'provider'

    provider = fields.Selection(
        PROVIDER_SELECTION,
        required=True,
        help='Where your company\'s email lives. Mail Pro sends and receives '
             'through this provider; change it here to move to another one.',
    )

    client_id = fields.Char(string='Client ID')
    client_secret_encrypted = fields.Char(
        string='Client Secret (Encrypted)',
        groups='base.group_system',
        copy=False,
        help='Encrypted — do not edit manually',
    )
    client_secret = fields.Char(
        string='Client Secret',
        compute='_compute_client_secret',
        inverse='_inverse_client_secret',
        store=False,
        groups='base.group_system',
        copy=False,
    )
    tenant_id = fields.Char(string='Tenant ID', help='Microsoft 365 only.')

    redirect_uri = fields.Char(
        string='Callback URL', compute='_compute_redirect_uri', readonly=True,
        help='Paste this into the provider console. Sign-in fails until it matches exactly.',
    )
    uses_oauth = fields.Boolean(compute='_compute_uses_oauth')
    # For IMAP these three read `pan.mail.account`, a different model, so
    # nothing declares that dependency to Odoo and nothing invalidates the
    # cache when an account changes. Fine for a view badge, which reads fresh
    # on every page load; wrong for a caller inside a longer transaction, who
    # should call `_credentials_present()` / `_has_connected_account()`
    # directly instead of trusting the field. `pan.mail.setup` does exactly
    # that.
    credentials_set = fields.Boolean(
        compute='_compute_status',
        help='Is there an application registration this provider can use?',
    )
    connected = fields.Boolean(
        compute='_compute_status',
        help='Has at least one account on this provider actually signed in?',
    )
    status = fields.Selection([
        ('not_configured', 'Not Configured'),
        ('not_connected', 'Not Connected'),
        ('connected', 'Connected'),
    ], compute='_compute_status')

    _provider_uniq = models.Constraint(
        'UNIQUE(provider)',
        'Each provider can only be registered once — edit the existing row.',
    )

    # -------------------------------------------------------------------------
    # Name
    # -------------------------------------------------------------------------

    @api.depends('provider')
    def _compute_display_name(self):
        """The provider's label, never its code.

        `_rec_name = 'provider'` hands the raw selection value to every place a
        record shows its name — a dropdown, a breadcrumb, the setup checklist —
        so the settings page read "outlook" where the provider's own form says
        "Microsoft 365". One compute fixes all of them at once.
        """
        labels = dict(PROVIDER_SELECTION)
        for record in self:
            record.display_name = labels.get(record.provider) or _('New Provider')

    # -------------------------------------------------------------------------
    # Application credentials
    # -------------------------------------------------------------------------

    @api.depends('client_secret_encrypted')
    def _compute_client_secret(self):
        for record in self:
            record.client_secret = SECRET_PLACEHOLDER if record.client_secret_encrypted else False

    def _inverse_client_secret(self):
        for record in self:
            if record.client_secret == SECRET_PLACEHOLDER:
                continue  # untouched by the admin
            record.client_secret_encrypted = encryption_utils.encrypt_value(
                self.env, record.client_secret,
            ) if record.client_secret else False
        # `create()` seeds this field's cache with the raw value it was given
        # rather than waiting for a compute, so the placeholder never
        # overwrites it on its own the way a `write()` would. Force it.
        self.invalidate_recordset(['client_secret'])

    @api.depends('provider')
    def _compute_redirect_uri(self):
        for record in self:
            record.redirect_uri = (
                oauth_redirect_uri(self.env, record.provider)
                if record.provider in OAUTH_CALLBACK_PATHS else False
            )

    @api.depends('provider')
    def _compute_uses_oauth(self):
        for record in self:
            record.uses_oauth = bool(record.provider) and get_provider_client(
                self.env, record.provider).uses_oauth

    # -------------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------------

    @api.depends('provider', 'client_id', 'client_secret_encrypted', 'tenant_id')
    def _compute_status(self):
        for record in self:
            record.credentials_set = record._credentials_present()
            record.connected = record._has_connected_account()
            if not record.credentials_set:
                record.status = 'not_configured'
            elif not record.connected:
                record.status = 'not_connected'
            else:
                record.status = 'connected'

    def _credentials_present(self):
        """Whether this provider has what it needs to be used.

        Microsoft and Google are judged by the application registration; IMAP
        has none, so it is judged by whether at least one account exists —
        the same question `pan.mail.setup` asked before this model existed.
        """
        self.ensure_one()
        if self.provider == 'outlook':
            return bool(self.client_id and self.client_secret_encrypted and self.tenant_id)
        if self.provider == 'gmail':
            return bool(self.client_id and self.client_secret_encrypted)
        if self.provider == 'imap':
            return bool(self._imap_accounts())
        return False

    def _has_connected_account(self):
        self.ensure_one()
        if not self.provider:
            return False
        accounts = self._imap_accounts() if self.provider == 'imap' else (
            self.env['pan.mail.account'].sudo().with_context(active_test=False).search(
                [('provider', '=', self.provider)]))
        return any(accounts.mapped('connected'))

    def _imap_accounts(self):
        self.ensure_one()
        return self.env['pan.mail.account'].sudo().with_context(active_test=False).search(
            [('provider', '=', 'imap')])

    # -------------------------------------------------------------------------
    # One row
    # -------------------------------------------------------------------------

    @api.model
    def current(self):
        """The provider this database is set up for, or an empty recordset.

        Every caller that used to search for the in-use row asks this instead,
        so "which provider" is answered in one place rather than by a domain
        repeated across the module.
        """
        return self.sudo().search([], limit=1)

    @api.model
    def current_code(self):
        """The provider code a new mailbox or account belongs to.

        A database runs on one provider, so asking again on every mailbox is a
        question with one possible answer. `DEFAULT_PROVIDER` is the fallback
        for the window before setup, where nothing has said yet.
        """
        return self.current().provider or DEFAULT_PROVIDER

    @api.constrains('provider')
    def _check_single_row(self):
        """A second provider is refused rather than silently ignored.

        Without this the table would hold rows nothing reads: `current()`
        takes the first, and the second would sit there looking configured.
        """
        if self.search_count([]) > 1:
            existing = self.search([('id', 'not in', self.ids)], limit=1)
            raise ValidationError(_(
                'Mail Pro runs on one provider. Change the existing "%s" row, '
                'or delete it first.'
            ) % dict(PROVIDER_SELECTION).get(existing.provider, existing.provider))
