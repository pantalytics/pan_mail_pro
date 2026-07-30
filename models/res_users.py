# -*- coding: utf-8 -*-
import logging

from odoo import fields, models, api, _
from .mail_provider_client import get_provider_client

_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    """Microsoft credentials moved to pan.mail.account; these fields are proxies.

    The x_microsoft_* token fields below no longer store anything. They read and
    write the user's Microsoft `pan.mail.account`, so every existing caller -
    the OAuth callback, the token refresh, mail_mail, the tests - keeps working
    unchanged while the credentials live in one place that a second provider can
    also use.

    The res_users columns still exist in the database, holding whatever the
    19.0.2.1.0 migration copied. Do not drop them yet: they are the rollback for
    this release, and Odoo leaves them alone now that the fields are unstored.
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

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + [
            'x_microsoft_default_mailbox_id',
            'x_microsoft_oauth_connected',
            'x_microsoft_health_status',
        ]

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + [
            'x_microsoft_default_mailbox_id',
            'x_microsoft_oauth_connected',
        ]

    # Microsoft OAuth tokens - proxies onto the user's Microsoft account
    x_microsoft_access_token_encrypted = fields.Char(
        string='Microsoft Access Token (Encrypted)',
        compute='_compute_microsoft_credentials',
        inverse='_inverse_access_token_encrypted',
        store=False,
        groups='base.group_system',
        copy=False,
        help='Encrypted access token - do not edit manually'
    )
    x_microsoft_refresh_token_encrypted = fields.Char(
        string='Microsoft Refresh Token (Encrypted)',
        compute='_compute_microsoft_credentials',
        inverse='_inverse_refresh_token_encrypted',
        store=False,
        groups='base.group_system',
        copy=False,
        help='Encrypted refresh token - do not edit manually'
    )
    x_microsoft_token_expiry = fields.Datetime(
        string='Token Expiry',
        compute='_compute_microsoft_credentials',
        inverse='_inverse_token_expiry',
        store=False,
        groups='base.group_system',
        copy=False
    )

    # Default mailbox for sending
    x_microsoft_default_mailbox_id = fields.Many2one(
        'x_microsoft.mailbox',
        string='Default Send From',
        help='Default mailbox to use when sending emails. You must have SendAs permission in Azure for this mailbox.'
    )

    # OAuth status (stored to allow domain filtering in mailbox config)
    x_microsoft_oauth_connected = fields.Boolean(
        string='Connected',
        compute='_compute_microsoft_oauth_connected',
        inverse='_inverse_microsoft_oauth_connected',
        store=True
    )

    # Health status for admin overview
    x_microsoft_health_status = fields.Selection([
        ('healthy', 'Healthy'),
        ('warning', 'Warning'),
        ('error', 'Error'),
        ('not_configured', 'Not Configured'),
    ], string='Microsoft Health', compute='_compute_microsoft_health_status', store=False)

    # Temporary OAuth state for CSRF protection (cleared after callback).
    # Deliberately NOT proxied onto pan.mail.account: the state is written when
    # the flow starts, which is before the account exists, and proxying it would
    # create an empty account every time someone opens the connect page and
    # walks away. It is a property of the browser round trip, not of credentials.
    x_microsoft_oauth_state = fields.Char(
        string='OAuth State',
        groups='base.group_system',
        copy=False,
        help='Temporary CSRF state token for OAuth flow'
    )

    # Google's own CSRF nonce - separate field so a user could, in principle,
    # have both flows in flight without one clobbering the other. Same rationale
    # as the Microsoft one: written before the account exists, so it lives here.
    x_google_oauth_state = fields.Char(
        string='Google OAuth State',
        groups='base.group_system',
        copy=False,
        help='Temporary CSRF state token for the Google OAuth flow'
    )

    x_google_oauth_connected = fields.Boolean(
        string='Google Connected',
        compute='_compute_google_oauth_connected',
        store=True,
        help='Whether this user has a connected Google account.'
    )

    # The provider-neutral version of the two flags above, and the one the
    # mailbox owner dropdowns filter on. A per-provider flag in a view domain is
    # how the module ended up listing Gmail owners it could not actually use;
    # "has usable credentials somewhere" is the question those domains mean.
    x_pan_mail_connected = fields.Boolean(
        string='Email Account Connected',
        compute='_compute_pan_mail_connected',
        store=True,
        help='Whether this user has a connected email account on any provider.'
    )

    # Computed fields for backwards compatibility (decrypt on read)
    x_microsoft_access_token = fields.Char(
        string='Microsoft Outlook Access Token',
        compute='_compute_decrypted_tokens',
        inverse='_inverse_access_token',
        store=False,  # Never store plain text in database
        groups='base.group_system',
        copy=False
    )
    x_microsoft_refresh_token = fields.Char(
        string='Microsoft Outlook Refresh Token',
        compute='_compute_decrypted_tokens',
        inverse='_inverse_refresh_token',
        store=False,  # Never store plain text in database
        groups='base.group_system',
        copy=False
    )

    # -------------------------------------------------------------------------
    # pan.mail.account plumbing
    #
    # Everything below reads and writes accounts with sudo(). The access rule
    # that matters is already on the res.users fields themselves
    # (groups='base.group_system'); re-checking it on the account would break
    # the cron and the mail queue, which read another user's token by design.
    # -------------------------------------------------------------------------

    def _microsoft_accounts(self):
        """Map user id -> Microsoft account, in one query for the whole recordset."""
        return self.env['pan.mail.account']._for_users(self, 'outlook')

    def _write_microsoft_credentials(self, values):
        """Write onto each user's Microsoft account, creating it when needed.

        Creating on demand is what makes a fresh OAuth connection land on an
        account without the controller knowing accounts exist. Clearing tokens
        for a user who never had an account creates nothing - a blank account
        would show up in the UI as a connection that was never made.
        """
        accounts = self._microsoft_accounts()
        for user in self:
            account = accounts.get(user.id)
            if not account:
                if not any(values.values()):
                    continue
                account = self.env['pan.mail.account'].sudo().create({
                    'provider': 'outlook',
                    'user_id': user.id,
                    'email': user.email or user.login,
                })
            account.write(values)

    @api.depends('x_pan_mail_account_ids.provider',
                 'x_pan_mail_account_ids.access_token_encrypted',
                 'x_pan_mail_account_ids.refresh_token_encrypted',
                 'x_pan_mail_account_ids.token_expiry')
    def _compute_microsoft_credentials(self):
        accounts = self._microsoft_accounts()
        for user in self:
            account = accounts.get(user.id)
            user.x_microsoft_access_token_encrypted = account.access_token_encrypted if account else False
            user.x_microsoft_refresh_token_encrypted = account.refresh_token_encrypted if account else False
            user.x_microsoft_token_expiry = account.token_expiry if account else False

    def _inverse_access_token_encrypted(self):
        for user in self:
            user._write_microsoft_credentials({
                'access_token_encrypted': user.x_microsoft_access_token_encrypted or False,
            })

    def _inverse_refresh_token_encrypted(self):
        for user in self:
            user._write_microsoft_credentials({
                'refresh_token_encrypted': user.x_microsoft_refresh_token_encrypted or False,
            })

    def _inverse_token_expiry(self):
        for user in self:
            user._write_microsoft_credentials({
                'token_expiry': user.x_microsoft_token_expiry or False,
            })

    @api.depends('x_pan_mail_account_ids.provider',
                 'x_pan_mail_account_ids.access_token_encrypted',
                 'x_pan_mail_account_ids.refresh_token_encrypted')
    def _compute_decrypted_tokens(self):
        """Plain-text view onto the account's tokens.

        The account decrypts; this model no longer knows the encryption scheme.
        """
        accounts = self._microsoft_accounts()
        for user in self:
            account = accounts.get(user.id)
            user.x_microsoft_access_token = account.access_token if account else False
            user.x_microsoft_refresh_token = account.refresh_token if account else False

    def _inverse_access_token(self):
        for user in self:
            user._write_microsoft_credentials({
                'access_token': user.x_microsoft_access_token or False,
            })

    def _inverse_refresh_token(self):
        for user in self:
            user._write_microsoft_credentials({
                'refresh_token': user.x_microsoft_refresh_token or False,
            })

    @api.depends('x_pan_mail_account_ids.provider',
                 'x_pan_mail_account_ids.refresh_token_encrypted')
    def _compute_microsoft_oauth_connected(self):
        """A refresh token on the Microsoft account is what "connected" means.

        This field is STORED and used in view domains (the mailbox owner
        dropdown). Getting the depends above wrong does not fail loudly - the
        field just stops recomputing, the dropdowns empty out, and sending falls
        back to the notification mailbox. Change with care.
        """
        accounts = self._microsoft_accounts()
        for user in self:
            account = accounts.get(user.id)
            user.x_microsoft_oauth_connected = bool(account and account.refresh_token_encrypted)

    def _inverse_microsoft_oauth_connected(self):
        """Disconnect when toggle is turned off"""
        for user in self:
            if not user.x_microsoft_oauth_connected:
                user.action_disconnect_microsoft()

    @api.depends('x_pan_mail_account_ids.provider',
                 'x_pan_mail_account_ids.refresh_token_encrypted')
    def _compute_google_oauth_connected(self):
        accounts = self.env['pan.mail.account']._for_users(self, 'gmail')
        for user in self:
            account = accounts.get(user.id)
            user.x_google_oauth_connected = bool(account and account.refresh_token_encrypted)

    @api.depends('x_pan_mail_account_ids.connected')
    def _compute_pan_mail_connected(self):
        for user in self:
            user.x_pan_mail_connected = any(
                account.connected for account in user.x_pan_mail_account_ids)

    def _compute_microsoft_health_status(self):
        """Compute Microsoft health status for admin overview."""
        Mailbox = self.env['x_microsoft.mailbox'].sudo()
        for user in self:
            # Get mailboxes where this user is owner
            user_mailboxes = Mailbox.search([('x_owner_user_id', '=', user.id)])

            if not user_mailboxes:
                # No mailboxes assigned - check if connected anyway
                if user.x_microsoft_oauth_connected:
                    user.x_microsoft_health_status = 'healthy'
                else:
                    user.x_microsoft_health_status = 'not_configured'
                continue

            # Has mailboxes - check status
            if not user.x_microsoft_oauth_connected:
                # Not connected but has mailboxes that need OAuth
                user.x_microsoft_health_status = 'error'
                continue

            # Connected - check mailbox health
            error_mailboxes = user_mailboxes.filtered(lambda m: m.x_health_status == 'error')
            warning_mailboxes = user_mailboxes.filtered(lambda m: m.x_health_status == 'warning')

            if error_mailboxes:
                user.x_microsoft_health_status = 'error'
            elif warning_mailboxes:
                user.x_microsoft_health_status = 'warning'
            else:
                user.x_microsoft_health_status = 'healthy'

    def action_send_connect_invite(self):
        """Button wrapper around `_send_connect_invites` for the user list."""
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

    def action_connect_microsoft(self):
        """
        Start OAuth flow by redirecting directly to Microsoft login.
        No intermediate wizard - goes straight to Microsoft.
        """
        self.ensure_one()

        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        redirect_uri = f"{base_url}/microsoft_oauth/callback"

        graph_client = get_provider_client(self.env)

        # Generate and store CSRF state token
        state = graph_client.generate_oauth_state()
        self.sudo().write({'x_microsoft_oauth_state': state})

        auth_url = graph_client.get_authorization_url(redirect_uri, state=state)

        return {
            'type': 'ir.actions.act_url',
            'url': auth_url,
            'target': 'new',
        }

    def action_disconnect_microsoft(self):
        """
        Disconnect Microsoft account by clearing all OAuth tokens.
        This method can be called directly from a button in the user form.
        """
        self.ensure_one()

        # Use sudo() because token fields have groups='base.group_system'
        self.sudo().write({
            'x_microsoft_access_token_encrypted': False,
            'x_microsoft_refresh_token_encrypted': False,
            'x_microsoft_token_expiry': False,
            'x_microsoft_default_mailbox_id': False,
            'x_microsoft_oauth_state': False,
        })

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Disconnected',
                'message': 'Microsoft account has been disconnected.',
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            }
        }

    def action_connect_google(self):
        """Start the Google OAuth flow, straight to the consent screen."""
        self.ensure_one()

        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        redirect_uri = f"{base_url}/google_oauth/callback"

        gmail_client = self.env['google.gmail.client']
        state = gmail_client.generate_oauth_state()
        self.sudo().write({'x_google_oauth_state': state})

        return {
            'type': 'ir.actions.act_url',
            'url': gmail_client.get_authorization_url(redirect_uri, state=state),
            'target': 'new',
        }

    def action_disconnect_google(self):
        """Disconnect the Google account by clearing its credentials.

        Unlike Microsoft, Google's tokens live only on the account (no res.users
        proxies), so this clears the account directly.
        """
        self.ensure_one()

        account = self.env['pan.mail.account'].sudo().with_context(active_test=False).search([
            ('user_id', '=', self.id), ('provider', '=', 'gmail'),
        ])
        account.write({
            'access_token_encrypted': False,
            'refresh_token_encrypted': False,
            'token_expiry': False,
            'oauth_state': False,
        })
        self.sudo().write({'x_google_oauth_state': False})

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Disconnected',
                'message': 'Google account has been disconnected.',
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            }
        }

