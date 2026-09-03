# -*- coding: utf-8 -*-
"""One row per provider registration, and which one is in use.

This is the application credential — the Azure app registration or the Google
Cloud OAuth client — not a person's login. A person's login is a
`pan.mail.account`. One provider can serve many accounts; every account of one
provider shares this one registration, because that is what the provider's
console actually asks for: one app, many users consenting to it.

`in_use` is the answer to "which provider is this database set up for?" —
what `pan_mail_pro.setup_provider` used to be as a bare config parameter.
Making it a field on a record rather than a global switch is what gives
switching providers back its old credentials for free: untick `in_use` here,
tick it on another row, and the first row's `client_id`/`client_secret` are
still sitting on it, unread rather than deleted. Retyping a Google client
secret because an admin tried Microsoft first and switched is not a cost
this module asks anyone to pay.

Not called `active`: Odoo treats a field with that exact name as its archive
convention and silently excludes `active=False` records from every plain
`search()` — which is precisely the state most rows are in most of the time
here, since only one provider is ever the chosen one. A search anybody forgot
to pass `active_test=False` to would quietly stop seeing every provider but
the active one, which is the opposite of what "switch back" needs.

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
    OAUTH_CALLBACK_PATHS,
    PROVIDER_SELECTION,
    get_provider_client,
    oauth_redirect_uri,
)

SECRET_PLACEHOLDER = '********'


class PanMailProvider(models.Model):
    _name = 'pan.mail.provider'
    _description = 'Provider Registration'
    _order = 'in_use desc, provider'
    _rec_name = 'provider'

    provider = fields.Selection(
        PROVIDER_SELECTION,
        required=True,
        help='Which provider this registration is for. One row per provider — '
             'switching does not lose the one you switch away from.',
    )
    in_use = fields.Boolean(
        string='In Use',
        default=False,
        help='The provider Mail Pro is set up for right now. Only one row can '
             'be in use; the rest keep their credentials for when you switch back.',
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

    _sql_constraints = [
        ('provider_uniq', 'unique(provider)',
         'Each provider can only be registered once — edit the existing row.'),
    ]

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
    # Only one provider in use
    # -------------------------------------------------------------------------

    @api.constrains('in_use')
    def _check_single_provider_in_use(self):
        for record in self:
            if not record.in_use:
                continue
            existing = self.search([
                ('in_use', '=', True), ('id', '!=', record.id),
            ], limit=1)
            if existing:
                raise ValidationError(_(
                    'Only one provider can be in use at a time. Untick "%s" first.'
                ) % dict(PROVIDER_SELECTION).get(existing.provider))
