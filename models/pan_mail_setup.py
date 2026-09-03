# -*- coding: utf-8 -*-
"""The two phases of the module's life.

Mail Pro is either being **set up** or **syncing**. There is nothing in
between, and no half-configured state that carries mail "as far as it can":
that was the shape that let a database sync incoming mail before anyone had
answered which domains are internal.

Five answers, in this order, make the difference:

1. provider      — where is the mail hosted
2. credentials   — the application registration for that provider
3. connection    — at least one account actually reaches it
4. domains       — which domains are ours, so colleagues can be told from
                   customers (there is no opt-out; see ARCHITECTURE.md §9.12)
5. notification  — one mailbox ticked as the one system mail goes out from

All five are mandatory. Until the last one is answered the phase is `setup`,
incoming sync does not run, and internal notifications queue with a readable
reason instead of being cancelled. The moment it is answered the phase is
`syncing` and nothing here has an opinion any more.

**The answers are about the database, not about you.** "Connected" means some
account on the selected provider is connected — not necessarily the admin
reading the settings page. A second admin opening that page must not be told
the product is unconfigured because *they* have not signed in yet; the settings
page keeps its own user-scoped question for the Connect button, and asks this
model for the phase.

Order is the contract. Step 5 creates a mailbox owned by whoever is setting up,
which is why step 3 comes first; step 4 comes before any mailbox exists because
a mailbox refuses to enable sync while the domains are unanswered, and meeting
that as a validation error after the fact is worse than being asked in order.
"""
import logging

from odoo import _, api, models

from .mail_provider_client import PARAM_SETUP_PROVIDER

_logger = logging.getLogger(__name__)

PHASE_SETUP = 'setup'
PHASE_SYNCING = 'syncing'

# What the settings page shows at the top. `error` is not a third phase: mail
# still flows, one mailbox has stopped. Keeping it out of `phase()` is what
# stops a single broken mailbox from switching the whole module off.
STATUS_ERROR = 'error'

STATUS_SELECTION = [
    (PHASE_SETUP, 'Setup'),
    (PHASE_SYNCING, 'Syncing'),
    (STATUS_ERROR, 'Attention needed'),
]

# The five steps, in the order they have to be answered. This tuple is the
# order: the settings page numbers its sections from it, and a step that moves
# changes what the steps after it may assume.
STEPS = (
    ('provider', 'Email provider'),
    ('credentials', 'Provider credentials'),
    ('connection', 'Connected account'),
    ('domains', 'Internal domains'),
    ('notification', 'A notification mailbox'),
)

# Where each OAuth provider's application credentials live. The client id and
# anything in `extra` are plain config parameters; the secret is
# Fernet-encrypted under its own key. A provider without a consent screen has
# no entry here at all — its credentials belong to one address, so "set up"
# means the accounts exist.
PROVIDER_CREDENTIALS = {
    'outlook': {
        'client_id': 'pan_mail_pro.microsoft_client_id',
        'secret': 'pan_mail_pro.microsoft_client_secret_encrypted',
        'extra': ('pan_mail_pro.microsoft_tenant_id',),
    },
    'gmail': {
        'client_id': 'pan_mail_pro.google_client_id',
        'secret': 'pan_mail_pro.google_client_secret_encrypted',
        'extra': (),
    },
}


class PanMailSetup(models.AbstractModel):
    _name = 'pan.mail.setup'
    _description = 'Mail Pro Setup Phase'

    # -------------------------------------------------------------------------
    # The answers
    # -------------------------------------------------------------------------

    @api.model
    def answers(self, provider=None):
        """The five answers as the database has them.

        `provider` overrides the stored choice, for the settings page: it has to
        report on the provider the admin is looking at, which during setup is
        not yet the one in the config parameter.
        """
        if provider is None:
            provider = self.env['ir.config_parameter'].sudo().get_param(PARAM_SETUP_PROVIDER)
        return {
            'provider': bool(provider),
            'credentials': self.credentials_set(provider),
            'connection': self.provider_is_connected(provider),
            'domains': self.env['pan.mail.domain'].is_configured(),
            'notification': self.notification_mailbox_usable(),
        }

    @api.model
    def credentials_set(self, provider):
        """Is there an application registration for this provider?"""
        if not provider:
            return False
        params = PROVIDER_CREDENTIALS.get(provider)
        if not params:
            # No consent screen, so there is no global credential to check:
            # the accounts themselves carry the login.
            return bool(self._provider_accounts(provider))
        ICP = self.env['ir.config_parameter'].sudo()
        required = (params['client_id'], params['secret']) + params['extra']
        return all(ICP.get_param(name) for name in required)

    @api.model
    def provider_is_connected(self, provider):
        """Can this database reach the provider at all?"""
        if not provider:
            return False
        return any(self._provider_accounts(provider).mapped('connected'))

    @api.model
    def notification_mailbox_usable(self):
        """Not "does the record exist" — can it actually send?"""
        mailbox = self.env['mail.mail']._notification_mailbox()
        return bool(mailbox) and mailbox._has_working_credentials()

    @api.model
    def _provider_accounts(self, provider):
        return self.env['pan.mail.account'].sudo().with_context(
            active_test=False).search([('provider', '=', provider)])

    # -------------------------------------------------------------------------
    # The phase
    # -------------------------------------------------------------------------

    @api.model
    def blocking_step(self, answers=None):
        """The first unanswered step as (index, code, label), or None."""
        if answers is None:
            answers = self.answers()
        for index, (code, label) in enumerate(STEPS, start=1):
            if not answers.get(code):
                return index, code, label
        return None

    @api.model
    def phase(self, answers=None):
        return PHASE_SETUP if self.blocking_step(answers) else PHASE_SYNCING

    @api.model
    def is_ready(self, answers=None):
        """True once all five steps are answered. Everything that carries mail
        asks this rather than checking a field of its own."""
        return self.phase(answers) == PHASE_SYNCING

    @api.model
    def blocking_step_label(self, answers=None):
        """One line for a banner: which step is holding setup up."""
        blocking = self.blocking_step(answers)
        if not blocking:
            return ''
        index, _code, label = blocking
        return _('Step %(index)s of %(total)s — %(label)s',
                 index=index, total=len(STEPS), label=label)

    # -------------------------------------------------------------------------
    # The status line
    # -------------------------------------------------------------------------

    @api.model
    def status(self, answers=None):
        """Setup, syncing, or syncing with something broken."""
        if not self.is_ready(answers):
            return PHASE_SETUP
        return STATUS_ERROR if self._mailboxes_in_error() else PHASE_SYNCING

    @api.model
    def status_detail(self, answers=None):
        """The one sentence under the status. Says what to do, not what broke."""
        if not self.is_ready(answers):
            blocking = self.blocking_step(answers)
            index, _code, label = blocking
            return _(
                '%(label)s — step %(index)s of %(total)s. Mail is not sent or '
                'received until all five are done.',
                label=label, index=index, total=len(STEPS),
            )
        broken = self._mailboxes_in_error()
        if broken:
            return _(
                '%(count)s mailbox(es) stopped syncing. The rest is unaffected.',
                count=len(broken),
            )
        syncing = self.env['pan.mail.mailbox'].sudo().search_count([
            ('sync_mode', '!=', 'none'),
        ])
        return _('Sending and receiving through %(count)s mailbox(es).', count=syncing)

    @api.model
    def _mailboxes_in_error(self):
        return self.env['pan.mail.mailbox'].sudo().search([('state', '=', 'error')])

    @api.model
    def not_ready_error(self):
        """The message shown to whoever tried to sync too early."""
        return _(
            'Mail Pro is still being set up (%(step)s). Incoming mail is not '
            'synced until all five setup steps are done — Settings → Mail Pro.',
            step=self.blocking_step_label(),
        )
