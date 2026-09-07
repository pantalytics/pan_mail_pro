# -*- coding: utf-8 -*-
"""The setup page.

Three mandatory steps in a fixed order, each of which reports whether the
*next* one can succeed — not whether somebody filled in a field. A
notification mailbox whose owner's token expired is not a done step. The
steps themselves, and the rule that turns them into a phase, live in
`pan_mail_setup.py`; this file is the checklist in front of them.

All three steps are tables now — providers, internal domains, mailboxes —
so this page only shows the answer and a way to reach the table where it is
actually edited. Nothing is typed here any more.

Inviting colleagues to connect is a real job but not a setup step, and its
button stays on the user list, next to the column that says who is still
missing. What this page adds is the *answer* -- how many have connected -- in
the same shape as the three steps: the number, and the way to the list where
something is done about it. Without it, setup finished on a page that never
mentioned the people the product is for.
"""
import logging

from odoo import _, api, fields, models

from .mail_provider_client import get_provider_client, get_setup_provider

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # -------------------------------------------------------------------------
    # Step 1 — the provider. Credentials and status live on `pan.mail.provider`,
    # its own table; this is a read-only pointer to the in-use row.
    # -------------------------------------------------------------------------
    x_active_provider_id = fields.Many2one(
        'pan.mail.provider', string='Email Provider',
        compute='_compute_setup_status', readonly=True,
    )
    x_setup_provider_done = fields.Boolean(compute='_compute_setup_status')

    # -------------------------------------------------------------------------
    # Step 2 — internal domains
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
    # Step 3 — the notification mailbox
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
    x_setup_domains_done = fields.Boolean(compute='_compute_setup_status')
    x_setup_notification_done = fields.Boolean(compute='_compute_setup_status')

    # -------------------------------------------------------------------------
    # After the three steps — the people
    #
    # Not a fourth step: the phase is answered without it, and mail flows for
    # whoever has connected. But a database where only the administrator ever
    # signed in is the most common way Mail Pro looks broken to everybody else,
    # and until now this page never said so.
    # -------------------------------------------------------------------------
    x_users_summary = fields.Char(compute='_compute_users_status')
    x_users_pending = fields.Integer(compute='_compute_users_status')
    x_users_relevant = fields.Boolean(
        compute='_compute_users_status',
        help='Whether users connect themselves at all. On a provider without a '
             'consent screen an administrator types the credentials, so there '
             'is nothing to ask them.',
    )

    # -------------------------------------------------------------------------
    # About
    # -------------------------------------------------------------------------
    x_module_version = fields.Char(
        string='Version',
        compute='_compute_module_version',
        help='The version of Mail Pro this database is running.',
    )

    def _compute_module_version(self):
        """The version the database is actually on, not the one in the source.

        `installed_version` is what `ir.module.module` recorded at the last
        upgrade. On an instance that pulled new code without a version bump the
        two differ, and the one that explains the behaviour on screen is this
        one. It is also the first thing to ask for in a support mail, which is
        why it is on the page rather than three clicks into Apps.
        """
        version = self.env['ir.module.module'].sudo().search(
            [('name', '=', 'pan_mail_pro')], limit=1).installed_version
        for record in self:
            record.x_module_version = version or ''

    def get_values(self):
        """Seed the domains tag field from the stored rows.

        `res.config.settings` is transient — a Many2many field is not filled in
        by `default_get()` the way a `config_parameter=` field would be, so it
        has to be read here explicitly.
        """
        res = super().get_values()
        res['x_internal_domain_ids'] = [
            (6, 0, self.env['pan.mail.domain'].sudo().search([]).ids)]
        return res

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

    @api.depends('x_internal_domain_ids')
    def _compute_setup_status(self):
        """Ask `pan.mail.setup` for the phase, with the form's answers on top.

        The domains answer can change while the admin is still typing — a tag
        added but not yet saved — so the record's own selection wins for that
        one. The provider and the mailboxes are read straight from their own
        tables: neither is edited on this page any more, so there is nothing
        of theirs still "on screen but not saved".
        """
        Setup = self.env['pan.mail.setup']
        alert = Setup.mailbox_alert()
        answers = Setup.answers()
        active_provider = self.env['pan.mail.provider'].current()

        for record in self:
            record.x_active_provider_id = active_provider
            record.x_setup_provider_done = bool(active_provider) and active_provider.credentials_set
            record.x_setup_domains_done = bool(record.x_internal_domain_ids)
            record.x_setup_notification_done = answers['mailboxes']
            record.x_notification_mailbox_id = self.env['mail.mail']._notification_mailbox()
            record.x_mailboxes_alert = alert

    # -------------------------------------------------------------------------
    # Who has connected
    # -------------------------------------------------------------------------

    def _compute_users_status(self):
        """How many of the people who need an account have one.

        Counted with the stored `x_pan_mail_connected` rather than by asking
        every user the banner's question, which is a per-user provider lookup
        this page has no reason to pay for. The population is the one the
        invitation reaches: internal, active, and with an address to mail.
        """
        provider = get_setup_provider(self.env)
        relevant = bool(provider) and get_provider_client(
            self.env, provider).uses_oauth

        total = connected = 0
        if relevant:
            Users = self.env['res.users'].sudo()
            domain = [
                ('share', '=', False),
                ('active', '=', True),
                ('id', '!=', self.env.ref('base.user_root').id),
                ('partner_id.email', '!=', False),
            ]
            total = Users.search_count(domain)
            connected = Users.search_count(
                domain + [('x_pan_mail_connected', '=', True)])

        summary = _('%(connected)s of %(total)s connected',
                    connected=connected, total=total) if total else ''
        for record in self:
            record.x_users_relevant = relevant and bool(total)
            record.x_users_summary = summary
            record.x_users_pending = total - connected
