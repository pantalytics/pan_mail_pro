# -*- coding: utf-8 -*-
import logging
import re
from odoo import fields, models, api, _
from odoo.exceptions import AccessError, ValidationError, UserError
from .mail_provider_client import (
    FOLDER_INBOX,
    PROVIDER_SELECTION,
    get_provider_client,
)
from .neutralization import database_is_neutralized

_logger = logging.getLogger(__name__)



class PanMailMailbox(models.Model):
    """An address Mail Pro sends from and, when its sync mode says so, reads.

    Provider-neutral: `provider` names the client that services it, and every
    question a caller has about credentials or capabilities goes through
    `_get_client()` rather than being answered here.
    """
    _name = 'pan.mail.mailbox'
    _description = 'Mailbox'
    _order = 'sequence, email'
    _rec_name = 'email'

    # Every mailbox is serviced by exactly one provider client, resolved
    # through the registry in mail_provider_client.py. It is inherited from
    # `pan.mail.provider`, not asked: the database runs on one provider, so a
    # picker on the mailbox form was a question whose answer was already known
    # and whose only other answers were wrong.
    provider = fields.Selection(
        PROVIDER_SELECTION,
        string='Provider',
        default=lambda self: self.env['pan.mail.provider'].current_code(),
        required=True,
        help='Email provider that services this mailbox.',
    )

    def _get_client(self):
        """Return the provider client for this mailbox."""
        self.ensure_one()
        return get_provider_client(self.env, self.provider)

    def _is_sendable_by(self, user):
        """Whether `user` may choose this mailbox as the sender of a mail.

        A personal mailbox sends with its *owner's* delegated token (see
        `_resolve_sending_user` in the Microsoft client), so letting anyone pick
        one lets any internal user send mail as a colleague, signed by that
        colleague's own token. Microsoft does not stop it: it is not a SendAs,
        it is the owner's credentials being used directly.

        The composer's view domain expresses the same rule, but a view domain is
        a convenience for the UI, never a boundary — the field is writable over
        RPC. This method is the boundary.

        Only personal mailboxes are restricted, and the notification mailbox is
        exempt even though it is one — see the comment on that branch.

        Shared mailboxes are shared on purpose.
        """
        self.ensure_one()
        if not self.active:
            return False
        # The notification mailbox is personal in every other respect — it has
        # an owner and sends with that owner's token — but every internal
        # notification goes out through it from any author, which is the job it
        # exists to do. The hole this method closes is sending as a named
        # colleague, not the company's system address behaving normally.
        if self.mailbox_type == 'personal' and not self.is_notification_mailbox:
            return bool(self.owner_user_id) and self.owner_user_id == user
        return True

    def _syncs_more_than_replies(self):
        """Whether this mailbox was asked for anything beyond reply threading.

        Every mailbox that can be read has its replies synced, because a reply
        belongs on the record it continues and that is not a preference. This
        asks the other question: did somebody switch on new conversations, or
        the Sent folder? That is what decides whether the mailbox *owes* working
        credentials and whether a draft state is worth a warning — a Microsoft
        shared mailbox that only sends has no credentials of its own and must
        not go red for it.

        Both halves are booleans, so a NULL — a row predating the field, a
        default that never ran — is False, and the unanswered question falls the
        safe way.
        """
        self.ensure_one()
        return self.sync_received or self.sync_sent

    def _needs_credentials(self):
        """Whether this mailbox needs credentials of its own to do its job.

        A Microsoft shared mailbox that only sends borrows the author's token,
        so it needs none — until it starts reading, which nobody can do on
        somebody else's behalf without being told whose token to use.
        """
        self.ensure_one()
        return self.mailbox_type == 'personal' or self.is_notification_mailbox or self._syncs_more_than_replies()

    def _has_working_credentials(self):
        """Whether this mailbox can actually reach its provider right now.

        Deliberately not "is the owner connected to Microsoft". Which credentials
        a mailbox runs on is the provider's decision: Microsoft reads a mailbox
        with its owner's delegated token, while a Gmail shared mailbox is its own
        Workspace account and has no owner to ask. Asking the client keeps that
        difference in the one place that is allowed to know about it.
        """
        self.ensure_one()
        return bool(self._get_client().resolve_receiving_account(self).connected)

    email = fields.Char(
        string='Email Address',
        required=True,
        help='Full email address of the mailbox (e.g., support@company.com)'
    )
    sequence = fields.Integer(
        string='Sequence',
        default=10,
        help='Display order in dropdowns'
    )
    active = fields.Boolean(
        string='Active',
        default=True,
        help='Uncheck to hide this mailbox from users'
    )
    # -------------------------------------------------------------------------
    # Mailbox Type Configuration
    # -------------------------------------------------------------------------
    mailbox_type = fields.Selection([
        ('personal', 'Personal'),
        ('shared', 'Shared'),
    ], string='Type', default='personal', required=True,
        help='Personal: Only the owner can send from this mailbox\n'
             'Shared: All users send from this address; which credentials are used '
             'depends on the provider')

    # Which mailbox sends the system email is a property of a mailbox, not a
    # third kind of mailbox. It used to be a Type value, which forced the
    # question "personal or shared?" to be answered "neither" and made every
    # rule about types carry an exception. A tick box on the one mailbox that
    # does the job says the same thing without the exception.
    # Named for the model it is not: `mail.mail.is_notification` is Odoo's own
    # flag for "this mail is a notification", and the two meet in mail_mail.py.
    is_notification_mailbox = fields.Boolean(
        string='Notification Mailbox',
        default=False,
        help='System emails — user invitations, password resets, activity '
             'reminders — are sent from this mailbox. Exactly one mailbox has '
             'this ticked.',
    )

    owner_user_id = fields.Many2one(
        'res.users',
        string='Owner',
        domain="[('x_pan_mail_connected', '=', True)]",
        help='The user whose credentials this mailbox sends with.',
        index=True
    )

    # -------------------------------------------------------------------------
    # Sync Configuration
    # -------------------------------------------------------------------------
    # One switch per direction, and each is asked in the words of the person
    # answering. "Received" is mail that arrived at this address; "sent" is mail
    # this address sent that Odoo did not write, which reaches us only by
    # reading the Sent folder back afterwards. Both used to be one three-way
    # `sync_mode`, where saying yes to receiving quietly said yes to copying
    # everything the owner wrote in Outlook as well.
    #
    # These are field names, not flow names. The *incoming flow* carries both of
    # them: reading Sent Items is a fetch, whatever direction the mail went.
    # ARCHITECTURE.md §1 keeps that distinction.
    # Not "should this mailbox be read". A reply to something Odoo already has
    # belongs on the record it continues, and nobody should have to switch that
    # on: a chatter thread showing the question and not the answer is the
    # failure this module exists to prevent. This switch is only about email
    # that starts a *new* conversation.
    sync_received = fields.Boolean(
        string='Sync Other Email',
        default=False,
        help='Email that arrives here without continuing a conversation Odoo '
             'already has. Replies are always synced.',
    )
    # Only meaningful while `sync_received` is on, which is why the form shows it
    # a level deeper. Kept as a plain stored field rather than folded back into
    # one three-way selection: on/off and how-wide are two questions, and the
    # second is the one nobody should have to answer to get started.
    sync_received_scope = fields.Selection([
        ('known_partners', 'Only from people who are already contacts'),
        ('all', 'From anyone, creating contacts as needed'),
    ], string='Which email should be synced?',
        default='known_partners', required=True,
        help='Whether senders who are not contacts yet are imported too. '
             '"From anyone" turns every sender into a contact, newsletters and '
             'private email included.')

    # No matching scope question, and for a different reason than receiving's.
    # Receiving has a second answer worth offering; sending has exactly one
    # case it can place with confidence, and that is a reply to a conversation
    # Odoo already holds. Mail that starts something new from a mail client has
    # no obvious home -- the contact, a lead, an opportunity -- and the module
    # is not in a position to guess. `_gate_wanted` refuses it.
    sync_sent = fields.Boolean(
        string='Sync Sent Items',
        default=False,
        help='Reads back the Sent Items folder of your own mail app (Outlook, '
             'Gmail or another client). Only replies to emails that are '
             'already in Odoo are synced; they land on the record they '
             'continue. Mail that starts a new conversation stays out.',
    )

    route_to_team = fields.Boolean(
        string='To Team',
        default=False,
        help='Route to a team instead of contact chatter'
    )

    # Keep for backwards compatibility / internal use
    sync_start_date = fields.Datetime(
        string='Start from',
        default=fields.Datetime.now,
        help='Import emails starting from this date. Default is today.'
    )
    last_sync_date = fields.Datetime(
        string='Last synced',
        readonly=True,
        help='Timestamp of last successful sync'
    )
    alias_id = fields.Many2one(
        'mail.alias',
        string='Route to Team',
        domain="[('alias_name', '!=', False)]",
        help='Select the team where emails should be routed. Teams configure their alias in their own settings (e.g., Sales Team → Alias).'
    )
    state = fields.Selection([
        ('draft', 'Not Configured'),
        ('active', 'Active'),
        ('error', 'Error'),
    ], string='Sync Status', default='draft', readonly=True)
    error_message = fields.Text(
        string='Last Error',
        readonly=True,
        help='Error message from last failed sync attempt'
    )

    # -------------------------------------------------------------------------
    # Health Status (computed for list view)
    # -------------------------------------------------------------------------
    health_status = fields.Selection([
        ('healthy', 'OK'),
        ('warning', 'Warning'),
        ('error', 'Error'),
    ], string='Status', compute='_compute_health_status', store=False)

    @api.depends('state', 'sync_received', 'sync_sent', 'mailbox_type', 'provider', 'owner_user_id',
                 'owner_user_id.x_pan_mail_account_ids.connected')
    def _compute_health_status(self):
        for record in self:
            if record.state == 'error':
                record.health_status = 'error'
            elif record._needs_credentials() and not record._has_working_credentials():
                record.health_status = 'error'
            elif record._syncs_more_than_replies() and record.state == 'draft':
                record.health_status = 'warning'
            else:
                record.health_status = 'healthy'

    def _no_credentials_error(self, sender=None):
        """Why this mailbox has no usable credentials, in the provider's terms.

        The single explanation for every "cannot send / cannot read" in the
        module. `sender` is the user whose token was expected, where the caller
        knows — on a Microsoft shared mailbox that is the author rather than the
        owner, and naming the wrong person sends an admin looking in the wrong
        place.
        """
        self.ensure_one()
        client = self._get_client()
        provider = client.provider_label()

        if self.mailbox_type == 'shared':
            if not client.supports_shared_mailbox:
                # Gmail and IMAP: a shared address is its own account, so there
                # is nothing an owner could connect on its behalf.
                return _(
                    'Shared mailbox "%(email)s" has no credentials of its own. On '
                    '%(provider)s a shared address is its own account, not a '
                    'delegation of someone else\'s.',
                    email=self.email, provider=provider,
                )
            who = sender or self.owner_user_id
            if not who:
                return _(
                    'Nobody is connected who could send from shared mailbox "%s".'
                ) % self.email
            return _(
                '"%(who)s" has no connected %(provider)s account, so nothing can '
                'send from shared mailbox "%(email)s". Connect it under My Profile '
                '→ Mail Pro, with SendAs rights on that address.',
                who=who.name, provider=provider, email=self.email,
            )

        if not self.owner_user_id:
            return _(
                'Mailbox "%s" has no Owner. Select the user whose account it '
                'sends and receives with.'
            ) % self.email
        return _(
            'Owner "%(owner)s" has no connected %(provider)s account. '
            'They must connect it first.',
            owner=self.owner_user_id.name, provider=provider,
        )

    def action_open_account(self):
        """Open the email account serving this mailbox, or a prefilled new one.

        Only meaningful for providers whose credentials are typed in rather than
        granted through a consent screen: an IMAP mailbox is useless until
        somebody enters its server and password, and this is the shortest path
        from the mailbox to that form.
        """
        self.ensure_one()
        account = self._get_client().resolve_receiving_account(self)
        action = {
            'type': 'ir.actions.act_window',
            'res_model': 'pan.mail.account',
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }
        if account:
            action['res_id'] = account.id
        else:
            action['context'] = {
                'default_email': self.email,
                'default_provider': self.provider,
                'default_user_id': self.owner_user_id.id,
            }
        return action

    def _check_manager(self):
        """Reading a live mailbox is a manager's act, whoever pressed the button.

        The model is readable by every internal user because shared and
        notification mailboxes are meant to be seen; that must not make
        `action_sync_now` a way for anyone to poll the company's mail.
        """
        if not self.env.su and not self.env.user.has_group(
                'pan_mail_pro.group_mail_mailbox_manager'):
            raise AccessError(_('Only a mailbox manager may sync a mailbox.'))

    def action_test_incoming(self):
        """Test incoming mail configuration by fetching a few messages."""
        self.ensure_one()
        self._check_manager()

        client = self._get_client()

        if not self._has_working_credentials():
            raise UserError(self._no_credentials_error())

        try:
            # Try to fetch 1 message to test connection
            messages = client.fetch_messages(
                account=client.resolve_receiving_account(self),
                mailbox=self,
                folder=FOLDER_INBOX,
                limit=1,
            )

            self.write({
                'state': 'active',
                'error_message': False,
            })

            return self._test_notification(
                _('Connection Successful'),
                _('Successfully connected to mailbox %s. Found %d message(s) in test.') % (
                    self.email, len(messages)),
                'success',
            )

        except Exception as e:
            self.write({
                'state': 'error',
                'error_message': str(e),
            })

            return self._test_notification(
                _('Connection Failed'), str(e), 'danger', sticky=True)

    def action_test_send(self):
        """Prove that mail actually leaves this mailbox, by sending one.

        The two tests next to this one read: `action_test_connection` asks the
        provider whether the credentials still work, `action_test_incoming`
        fetches a message. Neither says anything about *sending*, and sending
        is where a mailbox fails silently — a missing send scope, a SendAs
        permission that was never granted, an SPF or DMARC record that rejects
        the address. A consent screen the user just came back from proves none
        of those.

        **It always goes to whoever pressed the button, never to the mailbox's
        own address.** A mail addressed to the mailbox comes straight back in
        through the sync, which is a routing log entry and possibly a record
        created by a test. Sending it to the person instead also makes the
        result a thing a human can judge: it arrived, or it did not.

        On a personal mailbox that person *is* the owner, because
        `_is_sendable_by` lets nobody else send from one. So there is no
        per-type rule here: one recipient, and the sender boundary decides who
        may ask.

        The whole attempt is one savepoint. A failed test must not leave a
        `mail.mail` behind in the queue, where the cron would retry it and
        deliver a test email minutes later out of nowhere.
        """
        self.ensure_one()

        # "You may not do this" is raised; "it did not work" is reported below.
        # A refusal is something the reader can act on, and it is the same rule
        # `_mailbox_route` enforces again at send time — asked here so the
        # button says so instead of failing through the provider.
        if not self._is_sendable_by(self.env.user):
            raise UserError(_(
                'You cannot send from %s. A personal mailbox can only be used '
                'by its owner.'
            ) % self.email)

        recipient = self.env.user.email
        if not recipient:
            raise UserError(_(
                'Your user has no email address, so there is nowhere to send '
                'the test. Add one under My Profile and try again.'
            ))

        try:
            with self.env.cr.savepoint():
                # sudo: an ordinary internal user has no create access on
                # `mail.mail` (the composer creates theirs through
                # `message_post`). The sender boundary is the check above and
                # `_mailbox_route`'s, not this ACL.
                mail = self.env['mail.mail'].sudo().create({
                    'subject': _('Mail Pro test email'),
                    'body_html': _(
                        '<p>This is a test email from Odoo, sent through '
                        '<strong>%(mailbox)s</strong>.</p>'
                        '<p>You are reading it, so this mailbox can send.</p>',
                        mailbox=self.email,
                    ),
                    'email_from': self.email,
                    'email_to': recipient,
                    'author_id': self.env.user.partner_id.id,
                    'x_send_from_mailbox_id': self.id,
                })
                # raise_exception, so the reason arrives here rather than being
                # written to a row this savepoint is about to roll back.
                mail.send(raise_exception=True)
        except Exception as e:
            _logger.warning(
                "[Outgoing Mail] Test send from %s failed: %s", self.email, e)
            return self._test_notification(
                _('Test Email Failed'), str(e), 'danger', sticky=True)

        return self._test_notification(
            _('Test Email Sent'),
            _('Sent from %(mailbox)s to %(recipient)s. If it does not arrive, '
              'the problem is delivery rather than Odoo.',
              mailbox=self.email, recipient=recipient),
            'success',
        )

    @staticmethod
    def _test_notification(title, message, kind, sticky=False):
        """The display_notification the test buttons hand back."""
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'type': kind,
                'sticky': sticky,
            },
        }

    def action_sync_now(self):
        """Manually trigger email sync for this mailbox."""
        self.ensure_one()
        self._check_manager()

        if database_is_neutralized(self.env):
            raise UserError(_(
                'This database is neutralized (a staging or test copy). Syncing '
                'would read live mail and post notifications back out, so it is '
                'refused here.'
            ))

        setup = self.env['pan.mail.setup']
        if not setup.is_ready():
            raise UserError(setup.not_ready_error())

        if not self._has_working_credentials():
            raise UserError(self._no_credentials_error())

        # Trigger the processor for this mailbox
        processor = self.env['pan.mail.fetcher']
        processor._process_mailbox(self)

        # Mark as active on success (clear any previous error)
        if self.state != 'active':
            self.write({'state': 'active', 'error_message': False})

        # Reload the form to show updated status
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'pan.mail.mailbox',
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }

    @api.model_create_multi
    def create(self, vals_list):
        """Creating the first mailbox is what switches Mail Pro on.

        SMTP is taken over here rather than at install time. The module's
        graceful-degradation rule says a database with no mailboxes keeps using
        Odoo's own mail handling — but the install hook used to disable every
        outgoing mail server immediately, so a fresh install could not send the
        user invitations an admin needs *before* Mail Pro is configured. Now the
        takeover happens at the same moment routing does.
        """
        is_first = not self.sudo().with_context(active_test=False).search_count([])
        records = super().create(vals_list)
        if is_first:
            self._activate_smtp_takeover()
        return records

    @api.model
    def _activate_smtp_takeover(self):
        """Disable SMTP so mail cannot leave through two doors at once.

        Idempotent, and safe to call from both the install hook and create().
        """
        IrConfigParameter = self.env['ir.config_parameter'].sudo()
        if IrConfigParameter.get_param('pan_mail_pro.smtp_takeover_done') == 'True':
            return

        MailServer = self.env['ir.mail_server'].sudo().with_context(active_test=False)
        placeholder = self.env.ref(
            'pan_mail_pro.mail_server_disabled', raise_if_not_found=False
        )

        others = MailServer.search([('active', '=', True)])
        if placeholder:
            others -= placeholder
        if others:
            others.write({'active': False})
            for server in others:
                auth_type = getattr(server, 'smtp_authentication', 'login')
                extra_info = ' (Outlook OAuth)' if auth_type == 'outlook' else ''
                _logger.info(
                    f'[Mail Pro] Disabled SMTP server{extra_info}: {server.name} ({server.smtp_host})'
                )

        if placeholder and not placeholder.active:
            placeholder.write({'active': True})

        IrConfigParameter.set_param('base_setup.default_external_email_server', 'False')
        IrConfigParameter.set_param('pan_mail_pro.smtp_takeover_done', 'True')
        _logger.info('[Mail Pro] SMTP takeover active — all email routes through the provider API')

    def write(self, vals):
        """Reset last_sync_date when sync_start_date is moved to an earlier date."""
        rewind = self.browse()
        if 'sync_start_date' in vals and vals['sync_start_date']:
            new_start = fields.Datetime.to_datetime(vals['sync_start_date'])
            # Only the records that qualify, not the shared `vals`: one mailbox
            # that does must not move every other mailbox's cursor to `new_start`.
            rewind = self.filtered(
                lambda r: r.last_sync_date and new_start < r.last_sync_date)
        result = super().write(vals)
        if rewind:
            rewind.write({'last_sync_date': new_start})
        return result

    @api.onchange('sync_received', 'sync_sent')
    def _onchange_sync_switches(self):
        """Reset state when the mailbox stops syncing in either direction."""
        if not self._syncs_more_than_replies():
            self.state = 'draft'
            self.error_message = False

    @api.constrains('email')
    def _check_email_format(self):
        """Validate email format"""
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        for record in self:
            if record.email and not re.match(email_pattern, record.email):
                raise ValidationError(_('Invalid email address format: %s') % record.email)

    @api.constrains('email')
    def _check_email_unique(self):
        """One row per address, archived rows and letter case included.

        Two rows for one inbox is the failure: the clients resolve credentials
        by address with `limit=1`, so sends and the sync cursor would straddle
        whichever row the search happened to return.
        """
        for record in self:
            if record.email:
                existing = self.with_context(active_test=False).search([
                    ('email', '=ilike', record.email),
                    ('id', '!=', record.id)
                ], limit=1)
                if existing:
                    raise ValidationError(_('This email address is already registered!'))

    @api.constrains('mailbox_type', 'is_notification_mailbox', 'owner_user_id', 'sync_received', 'sync_sent', 'provider')
    def _check_owner_required(self):
        """Ensure an owner is set where the provider actually needs one."""
        for record in self:
            provider = record._get_client().provider_label()
            if (record.mailbox_type == 'personal' or record.is_notification_mailbox) \
                    and not record.owner_user_id:
                raise ValidationError(_(
                    '%(type)s mailbox requires an Owner. '
                    'Please select a user with %(provider)s connected.',
                    type=_('Notification') if record.is_notification_mailbox
                    else record.mailbox_type.capitalize(),
                    provider=provider,
                ))
            # A shared mailbox needs an owner only where reading it means
            # borrowing a person's delegated token. On Gmail the shared address
            # is its own Workspace account, so there is nobody to borrow from and
            # demanding an owner would make the mailbox unconfigurable.
            if (record.mailbox_type == 'shared' and
                    record._syncs_more_than_replies() and
                    not record.owner_user_id and
                    record._get_client().supports_shared_mailbox):
                raise ValidationError(_(
                    'Shared mailbox with sync enabled requires an Owner. '
                    'The Owner\'s %s account will be used to read emails.'
                ) % provider)

    @api.constrains('provider', 'mailbox_type')
    def _check_provider_supports_mailbox_type(self):
        """Providers differ in what they can service.

        Microsoft 365 has shared mailboxes (send-as with your own token);
        a provider without them must reject that configuration up front rather
        than failing at send time.
        """
        for record in self:
            record._get_client().check_mailbox_supported(record.mailbox_type)

    @api.constrains('is_notification_mailbox', 'owner_user_id', 'provider')
    def _check_notification_owner_is_connected(self):
        """On a consent-screen provider, the notification mailbox's owner has to
        have signed in — it sends with their grant and nothing else.

        This is where "has anybody connected yet?" belongs. It used to be a
        numbered setup step of its own, which asked the question in the
        abstract, in another screen, before there was anything to send. Here it
        is asked of the one mailbox that cannot do its job without an answer, at
        the moment somebody ticks the box.

        Only where the provider uses OAuth. On IMAP the credentials belong to
        the address rather than to a person, so the owner's own connection says
        nothing — asking it there would be the "is this user connected" mistake
        wearing a new hat, and would make the mailbox impossible to create
        before its account exists.

        Only on write of these fields, too: an owner who disconnects later must
        not block every unrelated edit. That case surfaces where it belongs —
        the phase reports `setup` again, because the mailbox can no longer send.
        """
        for record in self:
            if not record.is_notification_mailbox:
                continue
            if not record._get_client().uses_oauth:
                continue
            if not record.owner_user_id.x_pan_mail_connected:
                raise ValidationError(_(
                    'The notification mailbox sends with its owner\'s account, and '
                    '%(owner)s has not connected their mailbox yet. Ask them to open '
                    'the user menu at the top right → My Profile → Mail Pro, or pick '
                    'an owner who has.',
                    owner=record.owner_user_id.name or _('nobody'),
                ))

    @api.constrains('is_notification_mailbox')
    def _check_single_notification_mailbox(self):
        """Ensure only one notification mailbox exists."""
        for record in self:
            if record.is_notification_mailbox:
                existing = self.search([
                    ('is_notification_mailbox', '=', True),
                    ('id', '!=', record.id),
                    ('active', '=', True),
                ], limit=1)
                if existing:
                    raise ValidationError(_(
                        'Only one active Notification mailbox is allowed. '
                        'Existing notification mailbox: %s'
                    ) % existing.email)

    @api.constrains('email', 'sync_received', 'sync_sent')
    def _check_internal_domains_configured(self):
        """No mailbox at all before the internal domains exist.

        This is the gate, not the filter. The filter (`should_skip`) used to be
        the only line of defence and it failed open on an empty domain list, so
        a database that was never configured synced every internal email into
        Odoo. Blocking the *configuration* is what makes that unrepeatable; the
        runtime check in `_process_mailbox` only catches a list emptied later.

        It guards every mailbox rather than only the syncing ones, because a
        mailbox is the moment Mail Pro takes over the company's mail: the SMTP
        takeover fires here, sending starts here, and a database that gets this
        far without knowing its own domains is one switch away from copying
        internal mail into Odoo. Gating only the switch left the setting reading
        as an option belonging to sync, which is what it looked like at one
        customer right up until it mattered.
        """
        gate = self.env['pan.mail.domain'].configuration_error()
        if gate:
            raise ValidationError(gate)

    @api.constrains('sync_received', 'sync_sent')
    def _check_notification_mailbox_for_sync(self):
        """Ensure notification mailbox exists before any sync is switched on."""
        for record in self:
            if record._syncs_more_than_replies() and not record.is_notification_mailbox:
                notification_mailbox = self.search([
                    ('is_notification_mailbox', '=', True),
                    ('active', '=', True),
                ], limit=1)
                if not notification_mailbox:
                    raise ValidationError(_(
                        'A Notification mailbox is required before a mailbox can sync. '
                        'Tick "Notification Mailbox" on the mailbox that should send '
                        'system email first.'
                    ))

    @api.constrains('route_to_team', 'alias_id')
    def _check_alias_required_for_team_routing(self):
        """Ensure alias is set when route_to_team is enabled."""
        for record in self:
            if record.route_to_team and not record.alias_id:
                raise ValidationError(_(
                    'A Team must be selected when "Route to Team" is enabled.'
                ))
