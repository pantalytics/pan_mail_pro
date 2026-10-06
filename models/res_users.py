# -*- coding: utf-8 -*-
import logging

from odoo import fields, models, api, _
from odoo.exceptions import AccessError, UserError
from .neutralization import database_is_neutralized
from .mail_provider_client import (
    get_provider_client,
    oauth_redirect_uri,
)

_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    """A user's side of Mail Pro: their credentials, and where they send from.

    Credentials themselves live on `pan.mail.account`, one record per provider.
    This model used to mirror Microsoft's tokens in five unstored proxy fields
    so that callers written before accounts existed kept working; those callers
    are gone and so are the proxies. There is likewise one "connected" flag
    rather than one per provider - the question every caller actually asks is
    "can this person send mail", not "did they authorize Microsoft".
    """
    _inherit = 'res.users'

    x_pan_mail_account_ids = fields.One2many(
        'pan.mail.account',
        'user_id',
        string='Email Accounts',
        # Archived users get archived accounts; their credentials must still be
        # readable, or a stored recompute would silently report them disconnected.
        context={'active_test': False},
    )

    x_pan_mail_connected = fields.Boolean(
        string='Email Account Connected',
        compute='_compute_pan_mail_connected',
        store=True,
        help='Whether this user has a connected email account on any provider.',
    )

    # The address the provider reported at consent, which is not necessarily
    # the user's own: a person who signs in to Odoo as a colleague connects
    # whichever identity they consent with, and until this field nothing on
    # any screen said which. The account row always knew.
    x_pan_mail_connected_as = fields.Char(
        string='Connected as',
        compute='_compute_pan_mail_connected',
        store=True,
        help='The address the provider reported when this user signed in.',
    )
    x_default_mailbox_id = fields.Many2one(
        'pan.mail.mailbox',
        string='Default mailbox',
        help='The mailbox this user sends from unless they pick another one in the composer.',
    )

    # CSRF nonce for one authorization round trip. It lives here rather than on
    # the account because it is written when the flow *starts* - before the
    # account exists - and storing it there would create an empty account every
    # time somebody opened the connect page and walked away.
    x_pan_mail_oauth_state = fields.Char(
        string='OAuth State',
        groups='base.group_system',
        copy=False,
    )

    # The one mailbox setting a user owns. Everything else about a mailbox is
    # the workspace's (which address, whose credentials, which team an alias
    # routes to); how much of their own inbox Odoo reads back is theirs, and
    # asking an administrator for it makes a privacy decision somebody else's.
    # It is the mailbox's own `sync_level`, read and written through here so
    # there is one ladder and not a second copy of it.
    x_pan_mail_personal_mailbox_id = fields.Many2one(
        'pan.mail.mailbox',
        string='My Mailbox',
        compute='_compute_pan_mail_personal_mailbox',
        help='This user\'s own address. Shared mailboxes are the workspace\'s '
             'and are configured under Settings.',
    )

    x_pan_mail_sync_level = fields.Selection(
        selection=lambda self: self.env['pan.mail.mailbox']._fields['sync_level'].selection,
        string='Sync level',
        compute='_compute_pan_mail_sync_level',
        inverse='_inverse_pan_mail_sync_level',
        help='How much of this mailbox Odoo reads back. Replies to mail Odoo '
             'sent always land on their record; each level adds one more kind '
             'of mail on top of that.',
    )

    # Why Connect mailbox is not offered right now, in the reader's words,
    # or empty when it is. A plain user is sent to their administrator and
    # nowhere else: Settings > Mail Pro is a page they cannot open.
    x_pan_mail_connect_blocker = fields.Char(
        compute='_compute_pan_mail_connect_blocker',
        help='What has to happen before this user can connect a mailbox.',
    )

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + [
            'x_default_mailbox_id',
            'x_pan_mail_connect_blocker',
            'x_pan_mail_connected',
            'x_pan_mail_connected_as',
            'x_pan_mail_personal_mailbox_id',
            'x_pan_mail_sync_level',
        ]

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + [
            'x_default_mailbox_id',
            'x_pan_mail_sync_level',
        ]

    @api.depends('x_pan_mail_account_ids.connected', 'x_pan_mail_account_ids.email')
    def _compute_pan_mail_connected(self):
        for user in self:
            connected = user.x_pan_mail_account_ids.filtered('connected')
            user.x_pan_mail_connected = bool(connected)
            # One account per provider, and almost always one provider: the
            # first connected one is the identity this user sends as.
            user.x_pan_mail_connected_as = connected[:1].email or False

    # Not the mailbox's own fields: a dependency on a path through an unstored
    # many2one makes the ORM search `res.users` by that field to find whose
    # value to invalidate, and an unstored field cannot go in a WHERE clause.
    # Every write to any mailbox raises then. Connecting is the moment the row
    # appears, which is the only change of this answer the ORM can see.
    @api.depends('x_pan_mail_connected')
    def _compute_pan_mail_personal_mailbox(self):
        """The mailbox for this user's own address, if they have one.

        Searched rather than related: a personal mailbox is one whose owner
        signed in with that very address (`_compute_mailbox_type`), so the link
        runs the other way and there is no field on `res.users` to follow.
        Unstored for the same reason the mailbox cron stopped caching this kind
        of answer: a stored compute over a searched relation needs invalidation
        written by hand, and the hand-written half is what goes stale.

        The notification mailbox is excluded. It carries the system email, its
        Sync Settings tab is hidden on its own form, and it is the workspace's
        even when an administrator happens to own it.
        """
        Mailbox = self.env['pan.mail.mailbox'].sudo()
        for user in self:
            user.x_pan_mail_personal_mailbox_id = Mailbox.search([
                ('owner_user_id', '=', user.id),
                ('mailbox_type', '=', 'personal'),
                ('is_notification_mailbox', '=', False),
            ], limit=1)

    @api.depends('x_pan_mail_personal_mailbox_id')
    def _compute_pan_mail_sync_level(self):
        for user in self:
            user.x_pan_mail_sync_level = (
                user.x_pan_mail_personal_mailbox_id.sync_level or False)

    def _inverse_pan_mail_sync_level(self):
        """Write the ladder onto the mailbox itself.

        `sudo()` because the ACL gives write on `pan.mail.mailbox` to mailbox
        managers only, and this is the one field an ordinary user decides. The row is their own
        by construction of the compute, and `_check_mailbox_is_mine` is what
        stops the same write being aimed at a colleague over RPC.
        """
        for user in self:
            user._check_mailbox_is_mine()
            mailbox = user.x_pan_mail_personal_mailbox_id
            if mailbox and user.x_pan_mail_sync_level:
                mailbox.sudo().sync_level = user.x_pan_mail_sync_level

    # -------------------------------------------------------------------------
    # Connecting a mailbox
    #
    # One pair of actions for every provider. The provider decides what its
    # consent screen looks like and what a token means; this only decides who is
    # connecting and where they come back to.
    # -------------------------------------------------------------------------

    def _check_mailbox_is_mine(self):
        """Refuse to change somebody else's mailbox from their user record.

        These are public methods and self-writeable fields on `res.users`, so
        they are reachable over RPC for any id the caller can browse — and an
        internal user can browse every other user. Without this check, one
        employee could call `action_disconnect_mailbox()` on a colleague and
        wipe their tokens: that person cannot send until they walk through
        consent again, and aimed at whoever owns notifications@ it stops every
        system mail in the database. `action_connect_mailbox` is the same hole
        from the other side — it overwrites the CSRF nonce, which cancels a
        consent round somebody else is in the middle of. `x_pan_mail_sync_level`
        is the third: its inverse writes a colleague's mailbox under `sudo()`,
        so nothing below it would object.

        Administrators are exempt because reconnecting a mailbox on a user's
        behalf is a real support task, and so is `sudo()` for the setup flow.
        """
        self.ensure_one()
        if self.id == self.env.uid or self.env.su:
            return
        if self.env.user.has_group('base.group_system'):
            return
        _logger.warning(
            "[Mail Pro] User %s (id=%s) tried to change the mailbox of %s",
            self.env.user.login, self.env.user.id, self.login,
        )
        raise AccessError(_(
            'Only %(user)s can change that mailbox.', user=self.name))

    def action_connect_mailbox(self, provider=None):
        """Send this user to their provider's consent screen."""
        self.ensure_one()
        self._check_mailbox_is_mine()
        if self.share:
            # A portal login is a customer. Nothing in the OAuth round trip
            # asks who consented, so this is where a customer's Gmail is kept
            # from becoming a company mailbox the cron syncs.
            raise AccessError(_('Only internal users connect a mailbox.'))
        provider = provider or self.env['pan.mail.provider'].current().provider
        if not provider:
            raise UserError(_(
                'No email provider is set up yet. An administrator picks one '
                'under Settings > Mail Pro before anybody can connect.'
            ))
        client = get_provider_client(self.env, provider)
        if not client.uses_oauth:
            raise UserError(_(
                'An IMAP/SMTP mailbox has no sign-in screen. An administrator '
                'enters its server, login and password on the account.'
            ))
        License = self.env['pan.mail.license']
        if not License.sync_allowed() and not self.sudo().x_pan_mail_account_ids.filtered(
                lambda account: account.provider == provider):
            # Refused here, before the consent screen, with the same sentence
            # the account's create() would refuse with after it. Walking a
            # person through Microsoft's consent and then telling them no is
            # the one order this must never happen in.
            raise UserError(License.not_allowed_error())

        state = client.generate_oauth_state()
        self.sudo().write({'x_pan_mail_oauth_state': state})

        return {
            'type': 'ir.actions.act_url',
            'url': client.get_authorization_url(
                oauth_redirect_uri(self.env, provider), state=state),
            'target': 'new',
        }

    @api.depends_context('uid')
    def _compute_pan_mail_connect_blocker(self):
        blocker = self._pan_mail_connect_blocker()
        for user in self:
            user.x_pan_mail_connect_blocker = blocker

    @api.model
    def _pan_mail_connect_blocker(self, link=None, sync_allowed=None):
        """What stops `action_connect_mailbox` from reaching a consent screen.

        One sentence addressed to whoever is reading, or False when the button
        works. Four things have to be true, and each one is a way the button
        would otherwise end in an error dialog:

        - a provider is chosen *and* its application registration is complete,
          because the consent screen cannot be built without one
        - that provider has a consent screen at all: an IMAP/SMTP password is
          typed in by an administrator, so there is nothing for the user to click
        - the instance is connected to Pantalytics, because a new account is
          refused on one that is not
        - the internal domains are set, because the callback cannot claim the
          personal mailbox without them and the user would end up connected
          with no mailbox

        Deliberately not asked: whether the notification mailbox exists. The
        first person to connect is usually the administrator who is on that
        step and needs an owner for it, so a rule that waits for setup to be
        done waits for the thing it is meant to unblock.

        `link` is the Pantalytics link when the caller has it (`ir.http`
        resolves it once per page load and asks every question of that row);
        left out, `sync_allowed` looks it up. `sync_allowed` is that row's
        answer when the caller has already asked it -- `ir.http` again, which
        needs the same answer for a flag of its own -- and `None` means ask.
        """
        if self.env.user.has_group('base.group_system'):
            setup = _('Finish the setup under Settings > Mail Pro, then connect here.')
        else:
            setup = _('Ask your administrator to finish setting up Mail Pro.')
        provider = self.env['pan.mail.provider'].current().provider
        if not provider:
            return setup
        if not get_provider_client(self.env, provider).uses_oauth:
            return _('An IMAP/SMTP mailbox is connected by an administrator, '
                     'on the account.')
        if not self.env['pan.mail.setup'].credentials_set(provider):
            return setup
        if sync_allowed is None:
            License = self.env['pan.mail.license'] if link is None else link
            sync_allowed = License.sync_allowed()
        if not sync_allowed:
            return setup
        if not self.env['pan.mail.domain'].is_configured():
            return setup
        return False

    def _pan_mail_should_prompt_connect(self, link=None, sync_allowed=None):
        """Should this user be shown the "connect your mailbox" banner?

        Only where the button behind it would work: the user is internal and
        not connected yet, the database is not a neutralized copy (where
        connecting would hand a staging database real credentials), and
        `_pan_mail_connect_blocker` has nothing to say.
        """
        self.ensure_one()
        if not self._is_internal() or self.x_pan_mail_connected:
            return False
        if database_is_neutralized(self.env):
            return False
        return not self._pan_mail_connect_blocker(link=link, sync_allowed=sync_allowed)

    def action_disconnect_mailbox(self, provider=None):
        """Forget this user's stored credentials.

        Named provider, or all of them. It used to fall back to the database's
        setup provider, which reads as harmless until nobody has picked one:
        the domain then became `('provider', '=', False)`, matched no account,
        wiped nothing — and still returned "Your email account has been
        disconnected." A user who had just revoked access in Azure was told
        Odoo had let go of the tokens while it still held them.

        Disconnecting everything is also what the button claims: it says the
        account is disconnected, and `x_pan_mail_connected` — the flag it
        clears below — counts every provider, not the configured one.
        """
        self.ensure_one()
        self._check_mailbox_is_mine()

        domain = [('user_id', '=', self.id)]
        if provider:
            domain.append(('provider', '=', provider))

        self.env['pan.mail.account'].sudo().with_context(
            active_test=False).search(domain).write({
                'access_token_encrypted': False,
                'refresh_token_encrypted': False,
                'token_expiry': False,
                # IMAP's credential is a password, and "disconnected" has to
                # mean the same thing whichever provider issued the credential.
                'password_encrypted': False,
            })

        vals = {'x_pan_mail_oauth_state': False}
        # Only let go of the Send from mailbox once nothing is left to send
        # with. Disconnecting one of two providers is not a reason to forget a
        # choice the other one can still honour.
        if not self.x_pan_mail_connected:
            vals['x_default_mailbox_id'] = False
        self.sudo().write(vals)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Disconnected'),
                'message': _('Your email account has been disconnected.'),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    # -------------------------------------------------------------------------
    # Asking users to connect
    # -------------------------------------------------------------------------

    def action_send_connect_invite(self):
        """Button wrapper around `_send_connect_invites` for the user list.

        Asking colleagues to connect is an administrator's job, and this one
        sends mail to whoever it is pointed at — so it asks for the group
        rather than trusting the button it is normally reached from.
        """
        if not self.env.su and not self.env.user.has_group(
                'pan_mail_pro.group_mail_mailbox_manager'):
            raise AccessError(_(
                'Only a mailbox manager can ask users to connect their mailbox.'))
        sent = self._send_connect_invites()
        skipped = len(self) - sent
        message = _('Asked %d user(s) to connect their mailbox.') % sent
        if skipped:
            message += ' ' + _(
                '%d were skipped: already connected, or nothing to connect.'
            ) % skipped
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Invitations Sent'),
                'message': message,
                'type': 'success' if sent else 'warning',
                'sticky': False,
            },
        }

    def _send_connect_invites(self):
        """Email these users a one-click link to connect their mailbox.

        Only the users the link would actually help. Selecting everyone in the
        user list is the normal way to reach this, so the filter is here rather
        than in the admin's head: the same predicate that decides whether to
        show somebody the connect banner decides whether to mail them about it.
        Asking somebody who is already connected -- or who is on a provider with
        no consent screen, where an administrator types the password -- is a
        mail that can only confuse them.

        Queued rather than force-sent: during onboarding the notification
        mailbox may not be usable yet, and a queued invitation goes out by
        itself once it is. A force-send would just raise at the admin.

        Returns:
            int: number of invitations queued
        """
        template = self.env.ref(
            'pan_mail_pro.mail_template_connect_mailbox', raise_if_not_found=False
        )
        if not template:
            _logger.warning('[Mail Pro] Connect-invite template missing, nothing sent')
            return 0

        sent = 0
        for user in self:
            if not user.partner_id.email:
                _logger.info(f'[Mail Pro] Skipping connect invite for {user.name}: no email address')
                continue
            if not user._pan_mail_should_prompt_connect():
                _logger.info(
                    f'[Mail Pro] Skipping connect invite for {user.name}: '
                    'nothing for them to connect')
                continue
            template.send_mail(user.id, force_send=False)
            sent += 1

        _logger.info(f'[Mail Pro] Queued {sent} connect invitation(s)')
        return sent
