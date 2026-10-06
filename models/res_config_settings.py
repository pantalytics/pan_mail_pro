# -*- coding: utf-8 -*-
"""The setup page.

Four steps in a fixed order, each of which reports whether the *next* one can
succeed — not whether somebody filled in a field. A notification mailbox
whose owner's token expired is not a done step.

Step 1 is the Pantalytics account (`pan_mail_license.py`): nothing below it
exists until it is answered, because every step after it configures a product
that will not run, and a checklist you cannot finish reads as the thing that
is broken. Steps 2 to 4, and the rule that turns them into a phase, live in
`pan_mail_setup.py`; this file is the checklist in front of them.

Steps 2 to 4 are tables — providers, internal domains, mailboxes — so this
page only shows the answer and a way to reach the table where it is actually
edited. Nothing is typed here any more. Step 1 is the exception only while it
is open: its answer is then a button, because the thing it edits is a pairing
with our server, not a row. Once it is connected it is a line like the other
three — "Connected" and an arrow — and who connected it, the plan, usage and
billing and Disconnect are on the account page the arrow opens
(`pan.mail.license.action_open`).

An answer is one word or one number, never a list: the domains line counts
its domains and the mailboxes line counts its mailboxes, because a list does
not fit on a line and a count always does. The list is one arrow away.

Who has connected is not on this page: the invite button and the column that
says who is still missing are on the user list, and a count here repeated it.
"""
import logging

from odoo import _, api, fields, models

from .mail_provider_client import get_provider_client
from .pan_mail_license import IMPROVE_REFUSED_PARAM, STATUS_SELECTION

_logger = logging.getLogger(__name__)


def _counted(count, one, many):
    """A count as the answer on a checklist line: "1 domain", "3 domains"."""
    return one if count == 1 else many % count


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # -------------------------------------------------------------------------
    # Step 2 — the provider. Credentials and status live on `pan.mail.provider`,
    # its own table; this is a read-only pointer to the in-use row.
    # -------------------------------------------------------------------------
    x_active_provider_id = fields.Many2one(
        'pan.mail.provider', string='Email provider',
        compute='_compute_setup_status', readonly=True,
    )
    x_setup_provider_done = fields.Boolean(compute='_compute_setup_status')
    # What the open step says, and whether its arrow points at the accounts
    # list rather than the provider form: IMAP/SMTP has no registration, its
    # first step is done when the first account exists.
    x_setup_provider_todo = fields.Char(compute='_compute_setup_status')
    x_setup_provider_needs_account = fields.Boolean(compute='_compute_setup_status')

    # -------------------------------------------------------------------------
    # Step 3 — internal domains
    #
    # The one setting whose absence leaks data, so it is a gate rather than a
    # preference: incoming sync cannot be switched on until it is answered, one
    # way or the other. See pan_mail_domain.py.
    # -------------------------------------------------------------------------
    x_internal_domain_ids = fields.Many2many(
        'pan.mail.domain',
        string='Internal domains',
        help='Your own email domains. Mail between them is never synced into Odoo.',
    )
    # The count, not the list: "3 domains".
    x_internal_domains_summary = fields.Char(compute='_compute_internal_domains_status')
    x_internal_domains_suggested = fields.Char(compute='_compute_internal_domains_status')

    # -------------------------------------------------------------------------
    # Step 4 — the mailboxes. Done when one of them is ticked as the
    # notification mailbox and can send; the answer is how many there are,
    # and which one sends Odoo's own mail is a column of the list.
    # -------------------------------------------------------------------------
    x_mailboxes_summary = fields.Char(compute='_compute_setup_status')

    # -------------------------------------------------------------------------
    # Checklist state
    # -------------------------------------------------------------------------
    # A mailbox that stopped, in one sentence, on the mailboxes line of the
    # checklist. Empty when nothing is wrong.
    x_mailboxes_alert = fields.Char(compute='_compute_setup_status')
    x_setup_domains_done = fields.Boolean(compute='_compute_setup_status')
    x_setup_notification_done = fields.Boolean(compute='_compute_setup_status')

    # -------------------------------------------------------------------------
    # Step 1 — the Pantalytics account, see pan_mail_license.py. Done when the
    # link is entitled; a link that lapsed or was revoked is the red dot, not
    # the open one: it was answered and then broke, like a stopped mailbox.
    # -------------------------------------------------------------------------
    x_setup_account_done = fields.Boolean(compute='_compute_license')
    x_license_state = fields.Selection([
        ('not_connected', 'Not Connected'),
        ('pending', 'Pending'),
        ('connected', 'Connected'),
    ], compute='_compute_license')
    x_license_status = fields.Selection(
        STATUS_SELECTION, string='Status', compute='_compute_license')
    x_license_user_code = fields.Char(string='Code', compute='_compute_license')
    x_license_verify_url = fields.Char(string='Approve at', compute='_compute_license')
    x_license_last_error = fields.Char(compute='_compute_license')
    x_license_sync_blocked = fields.Boolean(compute='_compute_license')

    # Help improve Mail Pro, as the Pantalytics workspace decided it. This
    # page can only say no: the yes lives where the contract was signed.
    x_improve_on = fields.Boolean(compute='_compute_license')
    x_improve_refused = fields.Boolean(
        config_parameter=IMPROVE_REFUSED_PARAM,
        help='Refuse it for this Odoo instance, whatever the workspace decided. '
             'Nothing is reported or recorded from this instance while this is ticked.',
    )
    # The toggle on the page. It reads the positive way round, so what is
    # stored is its inverse: off here is the refusal.
    x_improve = fields.Boolean(
        string='Help improve Mail Pro',
        compute='_compute_improve', inverse='_inverse_improve',
    )

    # -------------------------------------------------------------------------
    # About
    # -------------------------------------------------------------------------
    x_module_version = fields.Char(
        string='Version',
        compute='_compute_module_version',
        help='The version of Mail Pro this Odoo instance is running.',
    )

    def _compute_module_version(self):
        """The version the database is actually on, not the one in the source.

        `latest_version` is what `ir.module.module` recorded at the last
        upgrade. On an instance that pulled new code without upgrading, that
        differs from the manifest on disk -- which Odoo, confusingly, calls
        `installed_version` -- and the one that explains the behaviour on
        screen is the database's. It is also the first thing to ask for in a
        support mail, which is why it is on the page rather than three clicks
        into Apps.
        """
        version = self.env['ir.module.module'].sudo().search(
            [('name', '=', 'pan_mail_pro')], limit=1).latest_version
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
    # Pantalytics account
    # -------------------------------------------------------------------------

    def _compute_license(self):
        link = self.env['pan.mail.license'].current()
        if not link or link.status == 'not_connected':
            state = 'not_connected'
        elif link.status == 'pending':
            state = 'pending'
        else:
            state = 'connected'
        blocked = not self.env['pan.mail.license'].sync_allowed()
        for record in self:
            record.x_license_sync_blocked = blocked
            record.x_license_state = state
            record.x_setup_account_done = state == 'connected' and not blocked
            record.x_license_status = link.status or 'not_connected'
            record.x_license_user_code = link.user_code
            record.x_license_verify_url = link.verify_url
            record.x_license_last_error = link.last_error
            record.x_improve_on = bool(
                link.improve and link.improve_host and link.improve_token)

    @api.depends('x_improve_on', 'x_improve_refused')
    def _compute_improve(self):
        for record in self:
            record.x_improve = record.x_improve_on and not record.x_improve_refused

    def _inverse_improve(self):
        for record in self:
            record.x_improve_refused = not record.x_improve

    def action_license_connect(self):
        link = self.env['pan.mail.license'].action_connect()
        return {'type': 'ir.actions.act_url', 'url': link.verify_url, 'target': 'new'}

    def action_license_check(self):
        return self.env['pan.mail.license'].current().action_check_approval()

    def action_license_disconnect(self):
        link = self.env['pan.mail.license'].current()
        if link:
            link.action_disconnect()

    def action_license_open(self):
        """The arrow on step 1: the account page."""
        return self.env['pan.mail.license'].current().action_open()

    # -------------------------------------------------------------------------
    # Internal domains
    # -------------------------------------------------------------------------

    @api.depends('x_internal_domain_ids')
    def _compute_internal_domains_status(self):
        """The list as a count, and what is left to suggest.

        Both read the record's own selection rather than the stored rows: the
        admin may have just clicked "Add" and the line has to follow along
        without a save.
        """
        suggested = self.env['pan.mail.domain'].suggest_domains()
        for record in self:
            selected = record.x_internal_domain_ids.mapped('name')
            record.x_internal_domains_summary = _counted(
                len(selected), _('1 domain'), _('%s domains'))
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
        mailboxes = _counted(self.env['pan.mail.mailbox'].sudo().search_count([]),
                             _('1 mailbox'), _('%s mailboxes'))

        for record in self:
            record.x_active_provider_id = active_provider
            record.x_setup_provider_done = bool(active_provider) and active_provider.credentials_set
            needs_account = bool(active_provider) and not get_provider_client(
                self.env, active_provider.provider).uses_oauth and not record.x_setup_provider_done
            record.x_setup_provider_needs_account = needs_account
            record.x_setup_provider_todo = (
                _('IMAP/SMTP: add the first account') if needs_account
                else _('Not set up yet'))
            record.x_setup_domains_done = bool(record.x_internal_domain_ids)
            record.x_setup_notification_done = answers['mailboxes']
            record.x_mailboxes_summary = mailboxes
            record.x_mailboxes_alert = alert
