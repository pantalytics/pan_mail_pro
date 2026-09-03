# -*- coding: utf-8 -*-
"""The two phases of the module's life.

Mail Pro is either being **set up** or **syncing**. There is nothing in
between, and no half-configured state that carries mail "as far as it can":
that was the shape that let a database sync incoming mail before anyone had
answered which domains are internal.

Three things have to be true:

1. provider   — a provider is chosen and its application credentials are
                complete, which is one answer: half a provider is no provider
2. domains    — which domains are ours, so colleagues can be told from
                customers (there is no opt-out; see ARCHITECTURE.md §9.12)
3. mailboxes  — one mailbox ticked as the one system mail goes out from, and
                able to send

Two questions used to be steps and are not. "Are the credentials filled in" is
not separate from "which provider": a provider without its registration cannot
do anything, so they are one answer. And "is anybody connected" is a property
of the notification mailbox — it needs an owner who has signed in, or it cannot
send — so step 3 already answers it, and the mailbox says so at the moment
somebody ticks the box.

All three are mandatory. Until the last one is answered the phase is `setup`,
incoming sync does not run, and internal notifications queue with a readable
reason instead of being cancelled. The moment it is answered the phase is
`syncing` and nothing here has an opinion any more.

**The answers are about the database, not about you.** "Connected" means some
account on the selected provider is connected — not necessarily the admin
reading the settings page. A second admin opening that page must not be told
the product is unconfigured because *they* have not signed in yet; the settings
page keeps its own user-scoped question for the Connect button, and asks this
model for the phase.

Order is the contract, and it is why the domains come before the mailboxes even
though a reader would name them the other way round: a mailbox refuses to be
created while the domains are unanswered, and meeting that as a validation
error after the fact is worse than being asked in order.
"""
import logging

from odoo import _, api, models

from .mail_provider_client import PARAM_SETUP_PROVIDER

_logger = logging.getLogger(__name__)

PHASE_SETUP = 'setup'
PHASE_SYNCING = 'syncing'

# The three, in the order they have to be answered. This tuple is the order:
# the settings page numbers its sections from it, and a step that moves changes
# what the steps after it may assume.
STEPS = (
    ('provider', 'Email provider'),
    ('domains', 'Internal domains'),
    ('mailboxes', 'A notification mailbox'),
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
        """The three answers as the database has them.

        `provider` overrides the stored choice, for the settings page: it has to
        report on the provider the admin is looking at, which during setup is
        not yet the one in the config parameter.
        """
        if provider is None:
            provider = self.env['ir.config_parameter'].sudo().get_param(PARAM_SETUP_PROVIDER)
        return {
            'provider': bool(provider) and self.credentials_set(provider),
            'domains': self.env['pan.mail.domain'].is_configured(),
            'mailboxes': self.notification_mailbox_usable(),
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
        """True once all three are answered. Everything that carries mail
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
    # Something broke after setup
    # -------------------------------------------------------------------------

    @api.model
    def mailbox_alert(self):
        """One sentence when a mailbox has stopped, or '' when none has.

        Not a phase and not a fourth step: mail still flows, one mailbox has
        stopped. It hangs off the mailboxes line of the checklist, which is the
        line that would otherwise show a green check while something is red.
        """
        broken = self._mailboxes_in_error()
        if not broken:
            return ''
        return _('%(count)s mailbox(es) stopped syncing. The rest is unaffected.',
                 count=len(broken))

    @api.model
    def _mailboxes_in_error(self):
        return self.env['pan.mail.mailbox'].sudo().search([('state', '=', 'error')])

    @api.model
    def not_ready_error(self):
        """The message shown to whoever tried to sync too early."""
        return _(
            'Mail Pro is still being set up (%(step)s). Incoming mail is not '
            'synced until every setup step is done — Settings → Mail Pro.',
            step=self.blocking_step_label(),
        )
