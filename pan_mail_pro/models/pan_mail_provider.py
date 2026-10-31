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

**Whose app.** Microsoft 365 signs in through the Pantalytics Mail Pro app by
default (`registration = 'pantalytics'`): a multitenant public client that
Pantalytics registered once, so a customer registers nothing in Azure. Its
client id comes from our platform with every sign-in it starts
(`pan.mail.license.microsoft_start`) and is kept here for the refresh; there
is no secret, the round carries a PKCE verifier instead, and the redirect URI
is the platform's, which sends the browser on to `/mail_pro/microsoft/return`.
The customer's own app registration (`'own'`) is the advanced path and works
exactly as before. A row created with registration fields is an own-app row.

The secret is Fernet-encrypted at rest, like every other secret in the module,
and it is never handed back to the browser once saved: `client_secret` reads
back a placeholder, and re-saving the placeholder is a no-op rather than
encrypting the placeholder itself.
"""
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.http import request

from . import encryption_utils
from .mail_provider_client import (
    DEFAULT_PROVIDER,
    OAUTH_CALLBACK_PATHS,
    PROVIDER_SELECTION,
    get_provider_client,
    oauth_redirect_uri,
    pkce_pair,
)

SECRET_PLACEHOLDER = '********'

# Whose application a Microsoft 365 sign-in goes through.
REGISTRATION_SELECTION = [
    ('pantalytics', 'Pantalytics Mail Pro app'),
    ('own', 'Your own app registration'),
]
# The fields only an own app registration has. A row created with any of them
# is an own-app row, whatever the default says.
OWN_REGISTRATION_FIELDS = ('client_id', 'client_secret', 'tenant_id')

# Where the platform sends the browser back to after Microsoft answered a
# sign-in through the Pantalytics app. Not registered anywhere: the platform
# is handed it as `return_url` with every start.
MS_APP_RETURN_PATH = '/mail_pro/microsoft/return'
# The one sign-in through the Pantalytics app in flight for this session: the
# state the platform issued, the PKCE verifier, the redirect URI the code is
# issued to, and whether it is an administrator's approval. In the session,
# like the confirm-it's-you round (controllers/main.py), so only the browser
# that started it can finish it.
MS_APP_SESSION_KEY = 'pan_mail_ms_app'


# What Azure accepts as the first path segment of a login URL: the directory
# GUID, a verified domain, or one of the three multi-tenant literals. Anything
# else is a value pasted into the wrong field, and the only place we can catch
# it is before it is saved -- once it is, it leaves the building as a path
# segment of a request to login.microsoftonline.com.
_GUID_RE = re.compile(r'^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')
_DOMAIN_RE = re.compile(r'^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?'
                        r'(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$')
TENANT_LITERALS = ('common', 'organizations', 'consumers')


def pantalytics_not_connected():
    """Why the Pantalytics app cannot be used right now. A function so the
    sentence is translated in the caller's language, not at import."""
    return _('Signing in with the Pantalytics Mail Pro app needs this Odoo '
             'connected to Pantalytics. An administrator connects it under '
             'Settings, Mail Pro, step 1.')


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

    registration = fields.Selection(
        REGISTRATION_SELECTION, string='Sign in through', default='pantalytics',
        help='Microsoft 365 only. The Pantalytics app needs nothing registered '
             'in Azure; your own app registration is for an organization that '
             'wants its own.',
    )
    pantalytics_client_id = fields.Char(
        string='Pantalytics app ID', readonly=True, copy=False,
        help='The Pantalytics app as the platform last named it. Refreshing a '
             'sign-in needs it.',
    )
    pantalytics_connected = fields.Boolean(compute='_compute_pantalytics_connected')

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

    @api.model_create_multi
    def create(self, vals_list):
        """A row created with an app registration's fields is an own-app row.

        The default is the Pantalytics app, which is right for a row created
        from the settings page. A row that arrives with a client id, secret
        or tenant (an import, a script, a test fixture, the seed) is somebody
        else's registration, and making it the Pantalytics app would drop
        what they typed.
        """
        for vals in vals_list:
            if 'registration' not in vals and any(
                    vals.get(name) for name in OWN_REGISTRATION_FIELDS):
                vals['registration'] = 'own'
        return super().create(vals_list)

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

    @api.depends('provider', 'registration')
    def _compute_redirect_uri(self):
        for record in self:
            record.redirect_uri = (
                oauth_redirect_uri(self.env, record.provider)
                if record.provider in OAUTH_CALLBACK_PATHS
                and not record.uses_pantalytics_app() else False
            )

    @api.depends('provider')
    def _compute_uses_oauth(self):
        for record in self:
            # An AbstractModel is an empty recordset, so the client itself is
            # falsy — `provider` is what says whether there is one to ask.
            client = get_provider_client(
                self.env, record.provider) if record.provider else None
            record.uses_oauth = bool(client is not None and client.uses_oauth)

    def verify_registration(self):
        """Ask the provider whether the registration just saved is real.

        Called by the form right after a save that touched the registration,
        so the admin sees the verdict in a dialog instead of discovering it at
        the consent screen after mailing their users. `status` alone cannot
        say it: "Not Connected" reads the same for a correct registration
        nobody has signed in to yet and for three fields of nonsense.

        Returns ``{'verified': bool | None, 'message': str}``. ``None`` means
        this provider has no check that runs without a user (Google), so
        signing in is the verification.
        """
        self.ensure_one()
        client = get_provider_client(self.env, self.provider)
        if not client.uses_oauth:
            raise UserError(_(
                '%s has no application registration to verify.'
            ) % client.provider_label())
        if self.uses_pantalytics_app():
            if not self._pantalytics_ready():
                return {'verified': False, 'message': pantalytics_not_connected()}
            return {
                'verified': None,
                'message': _('%s signs in through the Pantalytics Mail Pro app. '
                             'Signing in is the check.') % client.provider_label(),
            }
        if not client.supports_credential_test:
            return {
                'verified': None,
                'message': _('%s checks these at sign-in.') % client.provider_label(),
            }
        result = client.test_credentials()
        return {
            'verified': bool(result.get('success')),
            'message': result.get('message') or _('Unknown error'),
        }

    def action_connect_myself(self):
        """Walk the consent screen as the admin who is filling this form in.

        `verify_registration` proves the provider knows the registration. Only
        a real sign-in proves the rest of it: that the Callback URL above matches
        the one in the provider console, that the permissions were granted, and
        that this tenant allows users to consent at all. It is also the first
        connected account, which is what the mailbox setup needs next. The
        verify dialog offers it as its one next step.
        """
        self.ensure_one()
        return self.env.user.action_connect_mailbox(provider=self.provider)

    # -------------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------------

    @api.depends('provider', 'client_id', 'client_secret_encrypted', 'tenant_id',
                 'registration', 'pantalytics_client_id')
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
        if self.uses_pantalytics_app():
            return self._pantalytics_ready()
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
    # The Pantalytics app
    # -------------------------------------------------------------------------

    def uses_pantalytics_app(self):
        """Does this row sign in through the Pantalytics Mail Pro app?"""
        self.ensure_one()
        return self.provider == 'outlook' and self.registration == 'pantalytics'

    @api.depends_context('uid')
    def _compute_pantalytics_connected(self):
        connected = self._pantalytics_connected()
        for record in self:
            record.pantalytics_connected = connected

    @api.model
    def _pantalytics_connected(self):
        """Is this Odoo connected to Pantalytics, step 1, with a key our
        platform still accepts? Starting a sign-in through the app needs
        that key; nothing else here does."""
        link = self.env['pan.mail.license'].current()
        return bool(link) and bool(link.key_encrypted) and link.status not in (
            'not_connected', 'pending', 'invalid', 'revoked')

    def _pantalytics_ready(self):
        """Usable: connected to Pantalytics, or holding the app's id from an
        earlier sign-in, which is all a refresh needs. A lapsed connection
        must not read as a missing registration: sending is never gated on
        the connection (ARCHITECTURE.md §9.17), and the phase reads this."""
        self.ensure_one()
        return bool(self.pantalytics_client_id) or self._pantalytics_connected()

    @api.model
    def _pantalytics_row(self, provider):
        """The row of `provider` when it signs in through the Pantalytics
        app, or an empty recordset: the one question every sign-in start
        asks before it picks a path."""
        row = self.sudo().search([('provider', '=', provider)], limit=1)
        return row if row and row.uses_pantalytics_app() else self.browse()

    def _pantalytics_sign_in(self, login_hint=None, prompt=None, admin_consent=False):
        """Start one sign-in through the Pantalytics app: ``(url, state)``.

        Our platform issues the state and names the app and its redirect URI
        (`pan.mail.license.microsoft_start`); the authorization URL is built
        here, by the client, with a PKCE challenge whose verifier stays in
        this session. With `admin_consent` it is the organization-wide
        approval instead, which has no code and so no verifier.
        """
        self.ensure_one()
        if not request:
            raise UserError(_('A sign-in starts from a browser.'))
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        answer = self.env['pan.mail.license'].microsoft_start(base_url + MS_APP_RETURN_PATH)
        if self.pantalytics_client_id != answer['client_id']:
            self.sudo().pantalytics_client_id = answer['client_id']
        client = get_provider_client(self.env, self.provider)
        verifier = False
        if admin_consent:
            url = client.get_admin_consent_url(answer['redirect_uri'], answer['state'])
        else:
            verifier, challenge = pkce_pair()
            url = client.get_authorization_url(
                answer['redirect_uri'], state=answer['state'], login_hint=login_hint,
                prompt=prompt, code_challenge=challenge)
        request.session[MS_APP_SESSION_KEY] = {
            'state': answer['state'],
            'code_verifier': verifier,
            'redirect_uri': answer['redirect_uri'],
            'admin_consent': bool(admin_consent),
        }
        return url, answer['state']

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

    @api.constrains('provider', 'tenant_id')
    def _check_tenant_id(self):
        """Refuse a Tenant ID that cannot be one.

        Azure shows the Directory (tenant) ID, the Application (client) ID and
        the client secret on one screen, and the secret sits visually next to
        the other two. Pasting the secret into Tenant ID used to get as far as
        Microsoft, which answered AADSTS900023 -- their error, naming none of
        our fields, so the admin had to guess which of the three was wrong.
        Worse, the secret travelled there as a path segment of the login URL
        and has to be rotated afterwards.

        Same reasoning as the fail-closed domain gate: gate the configuration,
        not only the runtime. This is the last point at which the value is
        still inside the database.
        """
        for record in self:
            if record.provider != 'outlook':
                continue
            tenant = (record.tenant_id or '').strip()
            if tenant and not (
                tenant.lower() in TENANT_LITERALS
                or _GUID_RE.match(tenant)
                or _DOMAIN_RE.match(tenant)
            ):
                raise ValidationError(_(
                    'That is not a Tenant ID. Azure calls it the '
                    '"Directory (tenant) ID" on the app registration overview: '
                    'a GUID, or your verified domain. It is not the client '
                    'secret, which belongs in the Client Secret field.'
                ))
