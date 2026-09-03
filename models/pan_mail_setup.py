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


class PanMailSetup(models.AbstractModel):
    _name = 'pan.mail.setup'
    _description = 'Mail Pro Setup Phase'

    # -------------------------------------------------------------------------
    # The answers
    # -------------------------------------------------------------------------

    @api.model
    def answers(self, provider=None):
        """The three answers as the database has them.

        `provider` overrides which provider is judged, for a caller that wants
        to ask about one before it is the in-use row (a test, mainly — the
        settings page no longer edits credentials in place, so it never needs
        this any more than the other two steps do).
        """
        if provider is None:
            row = self.env['pan.mail.provider'].sudo().search([('in_use', '=', True)], limit=1)
            provider = row.provider if row else False
        return {
            'provider': bool(provider) and self.credentials_set(provider),
            'domains': self.env['pan.mail.domain'].is_configured(),
            'mailboxes': self.notification_mailbox_usable(),
        }

    @api.model
    def credentials_set(self, provider):
        """Is there an application registration for this provider?

        Delegates to the provider's own row — `pan.mail.provider` is where
        "outlook needs a tenant, gmail does not, imap needs neither" is decided
        now, and this asks it rather than repeating the rule. Calls the row's
        method directly rather than reading its `credentials_set` field: the
        field is cached like any compute, and IMAP's answer depends on
        `pan.mail.account` rows the field has no way to know changed. This is
        asked at the moment it matters, same as before this model existed, so
        it has to be right even inside a transaction that just created one.
        """
        if not provider:
            return False
        row = self.env['pan.mail.provider'].sudo().search(
            [('provider', '=', provider)], limit=1)
        return bool(row) and row._credentials_present()

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
