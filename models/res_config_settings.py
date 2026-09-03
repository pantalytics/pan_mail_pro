# -*- coding: utf-8 -*-
"""The setup page.

Five mandatory steps in a fixed order, each of which reports whether the *next*
one can succeed — not whether somebody filled in a field. A notification mailbox
whose owner's token expired is not a done step. The steps themselves, and the
rule that turns them into a phase, live in `pan_mail_setup.py`; this file is the
form in front of them.

The provider is asked first, because the steps genuinely differ per provider:
Azure wants a tenant, Google does not, and IMAP has no global credential at all.
Everything from step 4 on is provider-independent and never asks again.

Nothing else lives here. Inviting colleagues to connect is a real job but not a
setup step, and its button is on the user list, next to the column that says who
is still missing.
"""
import logging

from odoo import api, fields, models

from . import encryption_utils
from .pan_mail_setup import PROVIDER_CREDENTIALS
from .mail_provider_client import (
    PARAM_SETUP_PROVIDER,
    PROVIDER_SELECTION,
    get_provider_client,
    oauth_redirect_uri,
)

_logger = logging.getLogger(__name__)

# Shown instead of a secret that is already stored. Writing it back is a no-op,
# which is what lets the form round-trip without the admin retyping it.
SECRET_PLACEHOLDER = '********'


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # -------------------------------------------------------------------------
    # Step 1 — which provider
    # -------------------------------------------------------------------------
    x_mail_provider = fields.Selection(
        PROVIDER_SELECTION,
        string='Email Provider',
        config_parameter=PARAM_SETUP_PROVIDER,
        help='Where your email is hosted. Determines which setup steps are shown.',
    )

    # Provider-neutral state of that choice, so no step below has to know which
    # provider it is looking at.
    x_provider_credentials_set = fields.Boolean(compute='_compute_provider_state')
    x_provider_connected = fields.Boolean(compute='_compute_provider_state')
    x_provider_uses_oauth = fields.Boolean(compute='_compute_provider_state')

    # -------------------------------------------------------------------------
    # Step 2 — application credentials, one form per provider
    # -------------------------------------------------------------------------
    x_microsoft_client_id = fields.Char(
        string='Microsoft Client ID',
        help='Application (client) ID from your Azure app registration',
        config_parameter='pan_mail_pro.microsoft_client_id',
    )
    x_microsoft_client_secret = fields.Char(
        string='Microsoft Client Secret',
        compute='_compute_client_secrets',
        inverse='_inverse_microsoft_secret',
    )
    x_microsoft_tenant_id = fields.Char(
        string='Tenant ID',
        help='Directory (tenant) ID from your Azure app registration',
        config_parameter='pan_mail_pro.microsoft_tenant_id',
    )
    x_microsoft_redirect_uri = fields.Char(
        string='Redirect URI', compute='_compute_redirect_uris',
        help='Paste this into Azure → Authentication → Redirect URIs',
    )

    x_google_client_id = fields.Char(
        string='Google Client ID',
        help='OAuth client ID from the Google Cloud Console',
        config_parameter='pan_mail_pro.google_client_id',
    )
    x_google_client_secret = fields.Char(
        string='Google Client Secret',
        compute='_compute_client_secrets',
        inverse='_inverse_google_secret',
    )
    x_google_redirect_uri = fields.Char(
        string='Google Redirect URI', compute='_compute_redirect_uris',
        help='Paste this into Google Cloud → Credentials → Authorized redirect URIs',
    )

    # -------------------------------------------------------------------------
    # Step 4 — internal domains
    #
    # The one setting whose absence leaks data, so it is a gate rather than a
    # preference: incoming sync cannot be switched on until it is answered, one
    # way or the other. See pan_mail_domain.py.
    # -------------------------------------------------------------------------
    x_internal_domain_ids = fields.Many2many(
        'pan.mail.domain',
        string='Internal Domains',
        help='Your own email domains. Mail between them is never synced into Odoo.',
    )
    x_internal_domains_summary = fields.Char(compute='_compute_internal_domains_status')
    x_internal_domains_suggested = fields.Char(compute='_compute_internal_domains_status')

    # -------------------------------------------------------------------------
    # Step 5 — the notification mailbox
    # -------------------------------------------------------------------------
    x_notification_mailbox_id = fields.Many2one(
        'pan.mail.mailbox',
        string='Notification Mailbox',
        compute='_compute_setup_status',
        help='The mailbox with "Notification Mailbox" ticked, if there is one.',
    )

    # -------------------------------------------------------------------------
    # Checklist state
    # -------------------------------------------------------------------------
    # A mailbox that stopped, in one sentence, on the mailboxes line of the
    # checklist. Empty when nothing is wrong.
    x_mailboxes_alert = fields.Char(compute='_compute_setup_status')

    # Every step is one line: a status icon, its name, the answer itself — not
    # just the heading, or you have to open it again to see what you picked —
    # and the way to the place it is changed. For the domains and the mailboxes
    # that place is their own table; the provider is the only one that opens in
    # place, which is what this boolean does. Plain boolean on the transient
    # record, so a click is a client-side re-render and never saves the form;
    # saving rebuilds the record, which is what closes it again.
    x_edit_provider = fields.Boolean(default=False)
    x_setup_domains_done = fields.Boolean(compute='_compute_setup_status')
    x_setup_notification_done = fields.Boolean(compute='_compute_setup_status')
    x_setup_complete = fields.Boolean(compute='_compute_setup_status')

    # -------------------------------------------------------------------------
    # Provider state
    # -------------------------------------------------------------------------

    @api.depends('x_mail_provider',
                 'x_microsoft_client_id', 'x_microsoft_client_secret', 'x_microsoft_tenant_id',
                 'x_google_client_id', 'x_google_client_secret')
    def _compute_provider_state(self):
        """Is the selected provider set up, and am I connected to it?

        Reads the record's own fields rather than the config parameters, so the
        steps below the picker react while the admin is still typing.
        """
        for record in self:
            provider = record.x_mail_provider
            record.x_provider_uses_oauth = bool(
                provider and get_provider_client(self.env, provider).uses_oauth)

            if provider == 'outlook':
                record.x_provider_credentials_set = bool(
                    record.x_microsoft_client_id
                    and record.x_microsoft_tenant_id
                    and record.x_microsoft_client_secret
                )
            elif provider == 'gmail':
                record.x_provider_credentials_set = bool(
                    record.x_google_client_id and record.x_google_client_secret
                )
            elif provider == 'imap':
                # No global credential and no consent screen: an IMAP login
                # belongs to one address, so "set up" means the accounts exist.
                record.x_provider_credentials_set = bool(record._provider_accounts())
            else:
                record.x_provider_credentials_set = False

            record.x_provider_connected = record._provider_is_connected()

    def _provider_accounts(self):
        """Every account on the selected provider."""
        self.ensure_one()
        return self.env['pan.mail.account'].sudo().with_context(
            active_test=False).search([('provider', '=', self.x_mail_provider)])

    def _provider_is_connected(self):
        """Can anybody actually reach the selected provider yet?

        For OAuth providers that means the admin doing the setup; for IMAP it
        means at least one account has complete credentials, because there is
        nobody to send to a consent screen.
        """
        self.ensure_one()
        if not self.x_mail_provider:
            return False
        if self.x_provider_uses_oauth:
            return bool(self.env['pan.mail.account']._for_user(
                self.env.user, self.x_mail_provider).connected)
        return any(self._provider_accounts().mapped('connected'))

    def get_values(self):
        """Pre-select whichever provider already has credentials.

        A database configured before this picker existed has no stored choice.
        Landing it on an empty dropdown would read as "nothing is set up here"
        on an Azure tenant that has been sending mail for months.

        get_values() runs *after* default_get() has read the config parameters,
        so anything set here overrides a stored choice — only fill the gap when
        there is genuinely nothing stored.
        """
        res = super().get_values()
        res['x_internal_domain_ids'] = [
            (6, 0, self.env['pan.mail.domain'].sudo().search([]).ids)]
        ICP = self.env['ir.config_parameter'].sudo()
        if not ICP.get_param(PARAM_SETUP_PROVIDER):
            for provider, params in PROVIDER_CREDENTIALS.items():
                if ICP.get_param(params['client_id']):
                    res['x_mail_provider'] = provider
                    break
        return res

    # -------------------------------------------------------------------------
    # Application credentials
    # -------------------------------------------------------------------------

    def _compute_redirect_uris(self):
        for record in self:
            record.x_microsoft_redirect_uri = oauth_redirect_uri(self.env, 'outlook')
            record.x_google_redirect_uri = oauth_redirect_uri(self.env, 'gmail')

    def _compute_client_secrets(self):
        """Show a placeholder for a stored secret, never the secret itself."""
        ICP = self.env['ir.config_parameter'].sudo()
        for record in self:
            for provider, field in (('outlook', 'x_microsoft_client_secret'),
                                    ('gmail', 'x_google_client_secret')):
                stored = ICP.get_param(PROVIDER_CREDENTIALS[provider]['secret'])
                record[field] = SECRET_PLACEHOLDER if stored else False

    def _inverse_microsoft_secret(self):
        for record in self:
            record._store_secret('outlook', record.x_microsoft_client_secret)

    def _inverse_google_secret(self):
        for record in self:
            record._store_secret('gmail', record.x_google_client_secret)

    def _store_secret(self, provider, value):
        """Encrypt and store one provider's client secret."""
        if value == SECRET_PLACEHOLDER:
            return  # untouched by the admin
        self.env['ir.config_parameter'].sudo().set_param(
            PROVIDER_CREDENTIALS[provider]['secret'],
            encryption_utils.encrypt_value(self.env, value) if value else '',
        )

    # -------------------------------------------------------------------------
    # Internal domains
    # -------------------------------------------------------------------------

    @api.depends('x_internal_domain_ids')
    def _compute_internal_domains_status(self):
        """The list as one line, and what is left to suggest.

        Both read the record's own selection rather than the stored rows: the
        admin may have just clicked "Add" and the line has to follow along
        without a save.
        """
        suggested = self.env['pan.mail.domain'].suggest_domains()
        for record in self:
            selected = record.x_internal_domain_ids.mapped('name')
            record.x_internal_domains_summary = ', '.join(sorted(selected))
            record.x_internal_domains_suggested = ', '.join(
                d for d in suggested if d not in selected)


    def action_apply_suggested_internal_domains(self):
        """Add every domain we can derive from the database to the list.

        The click is the confirmation: the domains are rows, so this creates
        them and the settings page has nothing left to save. Returns nothing on
        purpose — the client re-reads this same transient record, so the line
        redraws with the new domains on it.
        """
        self.ensure_one()
        Domain = self.env['pan.mail.domain']
        names = Domain.suggest_domains()
        existing = Domain.sudo().search([('name', 'in', names)])
        missing = [n for n in names if n not in existing.mapped('name')]
        self.x_internal_domain_ids |= existing | Domain.sudo().create(
            [{'name': n} for n in missing])

    # -------------------------------------------------------------------------
    # Checklist
    # -------------------------------------------------------------------------

    @api.depends('x_mail_provider', 'x_provider_credentials_set', 'x_provider_connected',
                 'x_internal_domain_ids')
    def _compute_setup_status(self):
        """Ask `pan.mail.setup` for the phase, with the form's answers on top.

        Two of the three answers can change while the admin is still typing,
        so the record's own values win for those; the rest is what the
        database says. Without that overlay the page would keep reporting
        "not done" for a credential that is on screen but not yet saved.
        """
        Setup = self.env['pan.mail.setup']
        alert = Setup.mailbox_alert()

        for record in self:
            answers = Setup.answers(provider=record.x_mail_provider)
            answers['provider'] = bool(record.x_mail_provider) and record.x_provider_credentials_set
            answers['domains'] = bool(record.x_internal_domain_ids)

            record.x_setup_domains_done = answers['domains']
            record.x_setup_notification_done = answers['mailboxes']
            record.x_notification_mailbox_id = self.env['mail.mail']._notification_mailbox()
            record.x_mailboxes_alert = alert
