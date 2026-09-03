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

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from . import encryption_utils
from . import mail_mail
from . import pan_mail_internal_domains as internal_domains
from .pan_mail_setup import PROVIDER_CREDENTIALS, STATUS_SELECTION
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
    # way or the other. See pan_mail_internal_domains.py.
    # -------------------------------------------------------------------------
    x_internal_domains = fields.Char(
        string='Internal Domains',
        config_parameter=internal_domains.PARAM_DOMAINS,
        help='Your own email domains, comma separated (e.g. company.com, company.be). '
             'Email from these domains is not synced into Odoo.',
    )
    x_internal_domains_suggested = fields.Char(compute='_compute_internal_domains_status')
    x_internal_domains_uncovered = fields.Char(compute='_compute_internal_domains_status')

    # -------------------------------------------------------------------------
    # Step 5 — the notification mailbox
    # -------------------------------------------------------------------------
    x_notification_mailbox_email = fields.Char(
        string='Notification Address',
        default=lambda self: self._default_notification_mailbox_email(),
        help='Address system emails are sent from, e.g. notifications@company.com',
    )

    # Progressive disclosure. Once every step is answered the checklist folds
    # away and the page is a status line: a finished checklist is not what an
    # admin opens this page for. Plain boolean rather than a compute, so
    # toggling it is a client-side re-render and never saves the form.
    x_setup_show_steps = fields.Boolean(string='Show Setup Steps', default=False)

    # -------------------------------------------------------------------------
    # Checklist state
    # -------------------------------------------------------------------------
    x_setup_status = fields.Selection(
        # Labelled 'Mail Pro Status', not 'Status': res.config.settings carries
        # every installed module's fields, and a shared label is a registry
        # warning on every worker start. The page renders it with nolabel.
        STATUS_SELECTION, string='Mail Pro Status', compute='_compute_setup_status',
        help='Setup until all five steps are done, syncing from then on, and '
             'attention needed when a mailbox has stopped.',
    )
    x_setup_status_detail = fields.Char(compute='_compute_setup_status')
    x_setup_domains_done = fields.Boolean(compute='_compute_setup_status')
    x_setup_notification_done = fields.Boolean(compute='_compute_setup_status')
    x_setup_complete = fields.Boolean(compute='_compute_setup_status')
    x_setup_pending_notifications = fields.Integer(
        compute='_compute_setup_status',
        string='Emails Waiting for Setup',
    )

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

    @api.depends('x_internal_domains')
    def _compute_internal_domains_status(self):
        Domains = self.env['pan.mail.internal.domains']
        suggested = ', '.join(Domains.suggest_domains())
        uncovered = ', '.join(Domains.uncovered_domains())
        for record in self:
            # Read the form's value, not the saved parameter: the admin may be
            # typing domains right now and the warnings should follow along.
            configured = bool(Domains._parse(record.x_internal_domains))
            record.x_internal_domains_suggested = suggested
            record.x_internal_domains_uncovered = uncovered if configured else ''

    def set_values(self):
        """Refuse a domain list that leaves one of our own domains out.

        Checked after `super()` rather than before, so the question is asked of
        the value being saved rather than the one on the form; the raise rolls
        the write back with it.

        Only a *configured* list can be incomplete, so this never blocks the
        first save on an empty database. Absent and incomplete are two different
        failures with two different gates: the mailbox constraint catches the
        first, this catches the second, and the second is the more dangerous
        because it passes every check that asks whether the list is configured.
        """
        super().set_values()
        error = self.env['pan.mail.internal.domains'].completeness_error()
        if error:
            raise UserError(error)

    def action_apply_suggested_internal_domains(self):
        """Fill the domain list with everything we can derive from the database.

        The admin still has to save, so this is a suggestion they confirm rather
        than a setting that appears behind their back. Returns nothing on
        purpose: the client re-reads this same transient record, so the filled-in
        field survives — re-opening the settings action would build a fresh one
        from the saved parameters and throw this away.
        """
        self.ensure_one()
        self.x_internal_domains = self.x_internal_domains_suggested

    # -------------------------------------------------------------------------
    # Checklist
    # -------------------------------------------------------------------------

    @api.depends('x_mail_provider', 'x_provider_credentials_set', 'x_provider_connected',
                 'x_internal_domains')
    def _compute_setup_status(self):
        """Ask `pan.mail.setup` for the phase, with the form's answers on top.

        Two of the five answers can change while the admin is still typing, so
        the record's own values win for those; the rest is what the database
        says. Without that overlay the page would keep reporting "not done" for
        a credential that is on screen but not yet saved.
        """
        Setup = self.env['pan.mail.setup']
        pending = self.env['mail.mail'].sudo().search_count([
            ('state', '=', 'outgoing'),
            ('failure_reason', '=', mail_mail.NOTIFICATION_PENDING_REASON),
        ])

        Domains = self.env['pan.mail.internal.domains']
        for record in self:
            answers = Setup.answers(provider=record.x_mail_provider)
            answers['credentials'] = record.x_provider_credentials_set
            answers['domains'] = bool(Domains._parse(record.x_internal_domains))

            record.x_setup_domains_done = answers['domains']
            record.x_setup_notification_done = answers['notification']
            record.x_setup_status = Setup.status(answers)
            record.x_setup_status_detail = Setup.status_detail(answers)
            record.x_setup_complete = Setup.is_ready(answers)

            record.x_setup_pending_notifications = pending

    # -------------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------------

    def action_connect_provider(self):
        """Connect the admin's own account to the selected provider."""
        self.ensure_one()
        return self.env.user.action_connect_mailbox(self.x_mail_provider)

    def _default_notification_mailbox_email(self):
        """Pre-fill notifications@<your domain> so step 5 is one click."""
        Domains = self.env['pan.mail.internal.domains']
        domains = Domains.get_domains() or Domains.suggest_domains()
        return f'notifications@{domains[0]}' if domains else False

    def action_create_notification_mailbox(self):
        """Create notifications@ in one click, owned by whoever is setting up.

        Step 3 (connect your own account) comes first precisely so this button
        has a connected owner to point at — that is what breaks the
        chicken-and-egg where you need a working notification mailbox to invite
        the users whose accounts you need.
        """
        self.ensure_one()
        email = (self.x_notification_mailbox_email or '').strip()
        if not email:
            raise UserError(_('Please enter the address system emails should be sent from.'))
        if not self.x_mail_provider:
            raise UserError(_('Choose your email provider first.'))
        if not self.env.user.x_pan_mail_connected:
            raise UserError(_(
                'Connect your own email account first — the notification mailbox '
                'sends with its owner\'s credentials.'
            ))

        Mailbox = self.env['pan.mail.mailbox']
        existing = Mailbox.with_context(active_test=False).search([
            ('mailbox_type', '=', 'notification'),
        ], limit=1)
        if existing:
            raise UserError(_(
                'A Notification mailbox already exists (%s). Edit that one instead.'
            ) % existing.email)

        mailbox = Mailbox.create({
            'email': email,
            'mailbox_type': 'notification',
            'provider': self.x_mail_provider,
            'owner_user_id': self.env.user.id,
        })
        _logger.info(f"[Mail Pro] Created notification mailbox {mailbox.email} from setup checklist")

        return self._notify(
            _('Notification Mailbox Created'),
            _('System emails are now sent from %s.') % mailbox.email,
            # Stay on the settings page — navigating to the mailbox form would
            # discard whatever else the admin has typed.
            reload=True,
        )

    @staticmethod
    def _notify(title, message, reload=False):
        params = {'title': title, 'message': message, 'type': 'success', 'sticky': False}
        if reload:
            params['next'] = {'type': 'ir.actions.client', 'tag': 'soft_reload'}
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': params}
