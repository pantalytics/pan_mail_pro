# -*- coding: utf-8 -*-
import logging

from odoo import fields, models, api, _
from odoo.exceptions import AccessError, UserError
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

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + [
            'x_default_mailbox_id',
            'x_pan_mail_connected',
        ]

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + [
            'x_default_mailbox_id',
        ]

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

        state = client.generate_oauth_state()
        self.sudo().write({'x_pan_mail_oauth_state': state})

        return {
            'type': 'ir.actions.act_url',
            'url': client.get_authorization_url(
                oauth_redirect_uri(self.env, provider), state=state),
            'target': 'new',
        }

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
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Invitations Sent'),
                'message': _('Asked %d user(s) to connect their mailbox.') % sent,
                'type': 'success' if sent else 'warning',
                'sticky': False,
            },
        }

    def _send_connect_invites(self):
        """Email these users a one-click link to connect their mailbox.

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
            template.send_mail(user.id, force_send=False)
            sent += 1

        _logger.info(f'[Mail Pro] Queued {sent} connect invitation(s)')
        return sent
