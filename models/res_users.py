# -*- coding: utf-8 -*-
import logging

from odoo import fields, models, api, _
from odoo.exceptions import AccessError, UserError
from .neutralization import database_is_neutralized
from .mail_provider_client import (
    get_provider_client,
    get_setup_provider,
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

    x_default_mailbox_id = fields.Many2one(
        'pan.mail.mailbox',
        string='Default Send From',
        help='Mailbox this user sends from unless they pick another one in the composer.',
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

    # What Odoo reads from this person's own mailbox, on their own screen.
    # The setting lives on `pan.mail.mailbox`, which an internal user may read
    # and may not write -- and the one person who must be able to change it is
    # exactly that user. So it is surfaced here, where My Preferences can write
    # it, and the inverse is the boundary: you set your own, nobody else's.
    x_pan_mail_sync_level = fields.Selection(
        selection=lambda self: self.env['pan.mail.mailbox']._fields['sync_level'].selection,
        string='Odoo reads',
        compute='_compute_pan_mail_sync_level',
        inverse='_inverse_pan_mail_sync_level',
        help='How much of your own mailbox Odoo reads. Replies to mail Odoo '
             'sent always land on their record; each level adds one more kind '
             'of mail on top of that.',
    )

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + [
            'x_default_mailbox_id',
            'x_pan_mail_connected',
            'x_pan_mail_sync_level',
        ]

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + [
            'x_default_mailbox_id',
            'x_pan_mail_sync_level',
        ]

    def _own_personal_mailboxes(self):
        """The mailbox that is this user's own mail, not the company's.

        `sudo` on the search, not on what it is used for: a personal mailbox is
        invisible to everyone but its owner and a mailbox manager, and the
        administrator's overview reads this field for other people. What it
        exposes is one selection value, never a message.
        """
        self.ensure_one()
        # A form in the browser hands the compute a NewId, which no domain can
        # be built from; `_origin` is the saved record behind it, or nothing.
        user_id = self._origin.id
        if not isinstance(user_id, int):
            return self.env['pan.mail.mailbox']
        mailboxes = self.env['pan.mail.mailbox'].sudo().search([
            ('owner_user_id', '=', user_id),
            ('is_notification_mailbox', '=', False),
        ])
        # The first one, in the model's own order, and never more: the screen
        # shows one level, so writing one is the only thing it can honestly
        # mean. A second personal address is rare, and its level is set on the
        # mailbox itself. Raising a mailbox nobody was shown is the failure
        # this returns a single record to avoid.
        return mailboxes.filtered(lambda m: m.mailbox_type == 'personal')[:1]

    @api.depends('x_pan_mail_account_ids.email')
    def _compute_pan_mail_sync_level(self):
        for user in self:
            user.x_pan_mail_sync_level = user._own_personal_mailboxes().sync_level or False

    def _inverse_pan_mail_sync_level(self):
        for user in self:
            if not self.env.su and user != self.env.user:
                raise AccessError(_(
                    'Only %(user)s can change what Odoo reads from their '
                    'mailbox. You can lower it on the mailbox itself.',
                    user=user.name))
            if not user.x_pan_mail_sync_level:
                continue
            mailbox = user._own_personal_mailboxes()
            if mailbox:
                # sudo writes the value; the check above is the boundary, and
                # `pan.mail.mailbox.write` stamps `consent_date` because the
                # owner is still the acting user under sudo.
                mailbox.sudo().write({'sync_level': user.x_pan_mail_sync_level})

    @api.depends('x_pan_mail_account_ids.connected')
    def _compute_pan_mail_connected(self):
        for user in self:
            user.x_pan_mail_connected = any(
                account.connected for account in user.x_pan_mail_account_ids)

    # -------------------------------------------------------------------------
    # Connecting a mailbox
    #
    # One pair of actions for every provider. The provider decides what its
    # consent screen looks like and what a token means; this only decides who is
    # connecting and where they come back to.
    # -------------------------------------------------------------------------

    def _check_connection_is_mine(self):
        """Refuse to rewrite somebody else's stored credentials.

        These are public methods on `res.users`, so they are reachable over RPC
        for any id the caller can browse — and an internal user can browse every
        other user. Without this check, one employee could call
        `action_disconnect_mailbox()` on a colleague and wipe their tokens: that
        person cannot send until they walk through consent again, and aimed at
        whoever owns notifications@ it stops every system mail in the database.
        `action_connect_mailbox` is the same hole from the other side — it
        overwrites the CSRF nonce, which cancels a consent round somebody else
        is in the middle of.

        Administrators are exempt because reconnecting a mailbox on a user's
        behalf is a real support task, and so is `sudo()` for the setup flow.
        """
        self.ensure_one()
        if self.id == self.env.uid or self.env.su:
            return
        if self.env.user.has_group('base.group_system'):
            return
        _logger.warning(
            "[OAuth] User %s (id=%s) tried to change the mailbox connection of %s",
            self.env.user.login, self.env.user.id, self.login,
        )
        raise AccessError(_(
            'Only %(user)s can change that mailbox connection.', user=self.name))

    def action_connect_mailbox(self, provider=None):
        """Send this user to their provider's consent screen."""
        self.ensure_one()
        self._check_connection_is_mine()
        if self.share:
            # A portal login is a customer. Nothing in the OAuth round trip
            # asks who consented, so this is where a customer's Gmail is kept
            # from becoming a company mailbox the cron syncs.
            raise AccessError(_('Only internal users connect a mailbox.'))
        provider = provider or get_setup_provider(self.env)
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

    def _pan_mail_should_prompt_connect(self):
        """Should this user be shown the "connect your mailbox" banner?

        Only where the button behind it would work. Four things have to be
        true, and each one is a way the nudge would otherwise be a lie:

        - the user is internal and not connected yet -- the question itself
        - a provider is chosen *and* its application registration is complete,
          because `action_connect_mailbox` refuses without one and the consent
          screen cannot be built
        - that provider has a consent screen at all: an IMAP/SMTP password is
          typed in by an administrator, so there is nothing for the user to click
        - the database is not a neutralized copy, where connecting would hand a
          staging database real credentials

        Deliberately not asked: whether setup is finished. The first person to
        connect is usually the administrator who is on step 3 and needs an
        owner for the notification mailbox, so a banner that waits for setup to
        be done waits for the thing it is meant to unblock.
        """
        self.ensure_one()
        if not self._is_internal() or self.x_pan_mail_connected:
            return False
        if database_is_neutralized(self.env):
            return False
        if not self.env['pan.mail.license'].sync_allowed():
            # A new account is refused on an unconnected instance, so the
            # button would end in a refusal after the consent screen.
            return False
        provider = get_setup_provider(self.env)
        if not provider:
            return False
        if not get_provider_client(self.env, provider).uses_oauth:
            return False
        return self.env['pan.mail.setup'].credentials_set(provider)

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
        self._check_connection_is_mine()

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

    def action_test_send_mailbox(self):
        """Send a test email from this user's own sending address, to themselves.

        The one moment this answers is the moment after a user comes back from
        the consent screen: the grant proves Odoo may act on their behalf, and
        proves nothing about whether mail leaves. Sending from their own
        mailbox is the only thing that does.

        Only ever your own. An administrator pressing this on somebody else's
        user form would be asking to send from that person's personal mailbox,
        which `_is_sendable_by` refuses anyway — better to say so here than to
        offer a button that always fails.
        """
        self.ensure_one()
        if self != self.env.user:
            raise UserError(_(
                'A test email can only be sent from your own mailbox. Ask '
                '%s to press this on their own profile.'
            ) % self.name)
        if not self.x_default_mailbox_id:
            raise UserError(_(
                'Pick the address you send from first, then send the test.'
            ))
        return self.x_default_mailbox_id.action_test_send()

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
