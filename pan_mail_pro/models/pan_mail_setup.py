# -*- coding: utf-8 -*-
"""The two phases of the module's life.

Mail Pro is either being **set up** or **syncing**. There is nothing in
between, and no half-configured state that carries mail "as far as it can":
that was the shape that let a database sync incoming mail before anyone had
answered which domains are internal.

Three things have to be true:

2. provider   — a provider is chosen and its application credentials are
                complete, which is one answer: half a provider is no provider
3. domains    — which domains are ours, so colleagues can be told from
                customers (there is no opt-out; see ARCHITECTURE.md §9.12)
4. mailboxes  — one mailbox ticked as the one system mail goes out from, and
                able to send

They are numbered from 2 because step 1 on the settings page is the
Pantalytics account, and that one is not in this tuple on purpose: it is a
gate of its own (`pan.mail.license.sync_allowed()`), asked by the same
callers with a refusal that names the exact state the link is in, which a
generic "step 1 is open" would hide. This model only numbers its own steps
after it, so the banner and the page agree on which step is which.

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

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from .pan_mail_domain import PUBLIC_MAIL_DOMAINS

_logger = logging.getLogger(__name__)

# When the settings page first said "Mail Pro is up and running" on this
# database (#391). Set once; the confetti plays only while it is empty.
CELEBRATED_PARAM = 'pan_mail_pro.up_and_running_at'

PHASE_SETUP = 'setup'
PHASE_SYNCING = 'syncing'

# Step 1 on the settings page: the Pantalytics account. Not in STEPS (see the
# module docstring); the three below are numbered after it.
ACCOUNT_STEP = 1

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
        to ask about one before it is the row (a test, mainly — the settings
        page no longer edits credentials in place, so it never needs this any
        more than the other two steps do).
        """
        if provider is None:
            provider = self.env['pan.mail.provider'].current().provider or False
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
        """Not "does the record exist" — does it hold credentials to send with?

        This is the phase's question, and it stays the credentials one on
        purpose: the verified answer below can only be known after a probe
        and a send, and a phase that waited for those would stop every
        existing database's sync at the upgrade until both had happened.
        """
        mailbox = self.env['mail.mail']._notification_mailbox()
        return bool(mailbox) and mailbox._has_working_credentials()

    @api.model
    def notification_mailbox_verified(self):
        """Has the owner's sign-in been seen to read *and* send from it?

        What the checklist's dot on step 4 reads (`pan.mail.mailbox.access`).
        Returns ('ok' | 'open' | 'broken', sentence): `ok` when both answers
        are yes, `broken` with the provider's sentence when either is no,
        `open` with what is still to happen otherwise -- "not yet sent from"
        on Microsoft and SMTP, where only a send proves Send As, so the dot
        turns green on the first system email.
        """
        mailbox = self.env['mail.mail']._notification_mailbox()
        if not mailbox or not mailbox._has_working_credentials():
            return 'open', ''
        problem = mailbox._access_problem()
        if problem and problem[0] == 'error':
            return 'broken', problem[1]
        account = mailbox._get_client().resolve_receiving_account(mailbox)
        row = self.env['pan.mail.mailbox.access'].for_pair(mailbox, account)
        if row and row.can_read == 'yes' and row.can_send == 'yes':
            return 'ok', ''
        if row and row.can_read == 'yes':
            return 'open', _('Not yet sent from. It turns green after the first email.')
        return 'open', _('Not checked yet. The next sync run checks it.')

    @api.model
    def _provider_accounts(self, provider):
        return self.env['pan.mail.account'].sudo().with_context(
            active_test=False).search([('provider', '=', provider)])

    # -------------------------------------------------------------------------
    # The phase
    # -------------------------------------------------------------------------

    @api.model
    def blocking_step(self, answers=None):
        """The first unanswered step as (index, code, label), or None.

        The index is the number on the settings page, where the Pantalytics
        account is step 1, so the first step here is 2.
        """
        if answers is None:
            answers = self.answers()
        for index, (code, label) in enumerate(STEPS, start=ACCOUNT_STEP + 1):
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
                 index=index, total=ACCOUNT_STEP + len(STEPS), label=label)

    # -------------------------------------------------------------------------
    # Answers proposed instead of asked
    # -------------------------------------------------------------------------
    #
    # Steps 3 and 4 are questions the first sign-in already answers: the domain
    # the provider just verified is the company's, and the mailbox the admin
    # just connected can send. Proposed, never imposed: both only fill an empty
    # answer, and both are one Change away on the settings page
    # (docs/plans/onboarding-flow.md, "Fewer decisions").

    @api.model
    def propose_internal_domain(self, user, email):
        """The signed-in address's domain as the first internal domain.

        Only while the list is empty, only for a mailbox manager (the list is
        theirs to keep, `pan.mail.domain.set_domains`), and never a public mail
        domain: marking gmail.com internal would silently stop syncing every
        customer on Gmail. Nothing else is derived here, on purpose: the
        provider vouched for this one address, while alias domains and other
        users' addresses mean something else (ARCHITECTURE.md §9.12). A
        company's second domain is added by hand, on the same line.
        """
        Domain = self.env['pan.mail.domain'].sudo()
        if Domain.is_configured():
            return Domain.browse()
        if not user.has_group('pan_mail_pro.group_mail_mailbox_manager'):
            return Domain.browse()
        names = [n for n in Domain._parse(email) if n not in PUBLIC_MAIL_DOMAINS]
        if not names:
            return Domain.browse()
        _logger.info('[OAuth] Proposed %s as the internal domain', names[0])
        return Domain.create({'name': names[0]})

    @api.model
    def propose_notification_mailbox(self, user, email):
        """The admin's own mailbox as the one system mail goes out from.

        Only while no mailbox has the job and only for a mailbox manager: a
        plain user's sign-in must not decide where everybody's password resets
        come from. The mailbox's own constraints still apply (an owner who
        signed in, one notification mailbox), so a proposal that would break
        one is not made rather than forced.
        """
        Mailbox = self.env['pan.mail.mailbox'].sudo()
        if not user.has_group('pan_mail_pro.group_mail_mailbox_manager'):
            return Mailbox.browse()
        if Mailbox.search_count([('is_notification_mailbox', '=', True)], limit=1):
            return Mailbox.browse()
        mailbox = Mailbox.search(
            [('email', '=ilike', email), ('owner_user_id', '=', user.id)], limit=1)
        if not mailbox:
            return Mailbox.browse()
        mailbox.is_notification_mailbox = True
        _logger.info('[OAuth] Proposed %s as the notification mailbox', mailbox.email)
        return mailbox

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
        if broken:
            return _('%(count)s mailbox(es) stopped syncing. The rest is unaffected.',
                     count=len(broken))
        unreachable = self._mailboxes_refused()
        if unreachable:
            return _('%(count)s mailbox(es) the provider refuses: no mailbox at the '
                     'address, or the sign-in may not reach it. The rest is unaffected.',
                     count=len(unreachable))
        return ''

    @api.model
    def _mailboxes_in_error(self):
        return self.env['pan.mail.mailbox'].sudo().search([('state', '=', 'error')])

    @api.model
    def _mailboxes_refused(self):
        """The mailboxes the access table calls broken (`_access_problem`)."""
        mailboxes = self.env['pan.mail.mailbox'].sudo().search([('state', '!=', 'error')])
        return mailboxes.filtered(
            lambda m: (m._access_problem() or (None,))[0] == 'error')

    # -------------------------------------------------------------------------
    # The dots, and the end of the line
    # -------------------------------------------------------------------------
    #
    # What each step's dot on the settings page shows, decided here once: the
    # view draws the word, `up_and_running` asks whether all four are `ok`, so
    # the line that says "up and running" cannot disagree with the four dots
    # it replaces.

    @api.model
    def step_dots(self):
        """One word per step: `ok`, `attention` (amber), `warn` (red) or `todo`.

        Keyed `account`, `provider`, `domains`, `mailboxes`, in the order the
        page numbers them. Each rule is the one the settings page used to spell
        out in `invisible` expressions, and the docstrings of
        `res_config_settings.py` say why each colour is that colour.
        """
        return {
            'account': self._account_dot(),
            'provider': self._provider_dot(),
            'domains': 'ok' if self.env['pan.mail.domain'].is_configured() else 'todo',
            'mailboxes': self._mailboxes_dot(),
        }

    @api.model
    def _account_dot(self):
        """Step 1: red once it was connected and broke, or while incoming
        is paused for want of a card; the open dot before it was connected."""
        License = self.env['pan.mail.license']
        link = License.current()
        if not link or link.status in ('not_connected', 'pending'):
            return 'todo'
        if not License.sync_allowed() or link.incoming_paused():
            return 'warn'
        return 'ok'

    @api.model
    def _provider_dot(self):
        """Step 2: the provider row's own status, amber while nobody signed
        in or while the row is still on an own Azure app (#369)."""
        provider = self.env['pan.mail.provider'].current()
        # Fresh, not cached: `status` reads `pan.mail.account`, which its
        # compute cannot declare.
        provider.invalidate_recordset(['status', 'connected', 'credentials_set'])
        status = provider.status or 'not_configured'
        on_own_app = provider.provider == 'outlook' and provider.registration == 'own'
        if on_own_app or status == 'not_connected':
            return 'attention'
        return 'ok' if status == 'connected' else 'todo'

    @api.model
    def _mailboxes_dot(self):
        """Step 4: red when a mailbox stopped or the provider refused, amber
        while a personal mailbox carries the system email (#387), green once
        the owner's sign-in was seen to read and send from a shared one."""
        access, _sentence = self.notification_mailbox_verified()
        if self.mailbox_alert() or access == 'broken':
            return 'warn'
        if not self.notification_mailbox_usable():
            return 'todo'
        if self.env['mail.mail']._notification_mailbox().provider_type == 'personal':
            return 'attention'
        return 'ok' if access == 'ok' else 'todo'

    @api.model
    def first_mail_on_record(self):
        """Has an incoming mail landed on a record that is not a contact?

        The platform's station 4 (docs/plans/onboarding-flow.md), read the
        way link coverage reads "linked to a document" (ARCHITECTURE.md §7):
        a `mail.message` Mail Pro received, on a model other than a contact.
        Off the messages rather than the routing log, because the log is
        cleaned after 90 days and a mail linked by hand is on a record too.
        """
        domain = [('x_direction', '=', 'incoming')] + \
            self.env['pan.mail.coverage']._linked_domain()
        return bool(self.env['mail.message'].sudo().search(domain, limit=1))

    @api.model
    def up_and_running(self, dots=None):
        """Four steps done and the first mail on a record (#391).

        Done is green, and for step 4 amber too: a personal mailbox carrying
        the system email works, and a shared one is a recommendation, not a
        failure (#387). The line says so under its headline. Red or the open
        dot blocks. Green step 4 alone is not the end: a mailbox that can
        send has not yet shown that mail arrives where it belongs. Not sticky
        either: a step that turns red later opens the checklist again,
        because that is where the red dot is.
        """
        if dots is None:
            dots = self.step_dots()
        done = all(dot == 'ok' or (step, dot) == ('mailboxes', 'attention')
                   for step, dot in dots.items())
        return done and self.first_mail_on_record()

    @api.model
    def celebrate_once(self):
        """True the first time the end of the line is shown on this database.

        The confetti is once per database, not per person and not per page
        load, so the moment is a config parameter. Asked by the settings page
        when it draws "Mail Pro is up and running"; it answers False when the
        database is not there, so a call from anywhere else cannot spend it.
        """
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Only an administrator can open the Mail Pro settings.'))
        Params = self.env['ir.config_parameter'].sudo()
        if Params.get_param(CELEBRATED_PARAM) or not self.up_and_running():
            return False
        Params.set_param(CELEBRATED_PARAM, fields.Datetime.to_string(fields.Datetime.now()))
        _logger.info('[Mail Pro] Up and running')
        return True

    @api.model
    def action_invite_colleagues(self):
        """The one next action once Mail Pro runs: the internal users who
        have not connected a mailbox, on Odoo's own user list, where Send
        Mail Pro invite already mails each of them the connect link."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Invite your colleagues'),
            'res_model': 'res.users',
            'views': [(self.env.ref('base.view_users_tree').id, 'list'),
                      (self.env.ref('base.view_users_form').id, 'form')],
            'search_view_id': [self.env.ref('base.view_users_search').id],
            'domain': self._unconnected_colleagues_domain(),
            'context': {'is_action_res_users': True},
            'target': 'current',
        }

    @api.model
    def _unconnected_colleagues_domain(self):
        return [('share', '=', False), ('x_pan_mail_connected', '=', False)]

    @api.model
    def not_ready_error(self):
        """The message shown to whoever tried to sync too early."""
        return _(
            'Mail Pro is still being set up (%(step)s). Incoming mail is not '
            'synced until every setup step is done — Settings → Mail Pro.',
            step=self.blocking_step_label(),
        )
