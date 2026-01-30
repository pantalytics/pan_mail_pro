# -*- coding: utf-8 -*-
from odoo import fields, models, api
from . import encryption_utils


class ResUsers(models.Model):
    _inherit = 'res.users'

    # Microsoft OAuth tokens (stored encrypted in database)
    x_microsoft_access_token_encrypted = fields.Char(
        string='Microsoft Access Token (Encrypted)',
        groups='base.group_system',
        copy=False,
        help='Encrypted access token - do not edit manually'
    )
    x_microsoft_refresh_token_encrypted = fields.Char(
        string='Microsoft Refresh Token (Encrypted)',
        groups='base.group_system',
        copy=False,
        help='Encrypted refresh token - do not edit manually'
    )
    x_microsoft_token_expiry = fields.Datetime(
        string='Token Expiry',
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
        string='Microsoft Connected',
        compute='_compute_microsoft_oauth_connected',
        store=True
    )

    # Health status for admin overview
    x_microsoft_health_status = fields.Selection([
        ('healthy', 'Healthy'),
        ('warning', 'Warning'),
        ('error', 'Error'),
        ('not_configured', 'Not Configured'),
    ], string='Microsoft Health', compute='_compute_microsoft_health_status', store=False)

    # Temporary OAuth state for CSRF protection (cleared after callback)
    x_microsoft_oauth_state = fields.Char(
        string='OAuth State',
        groups='base.group_system',
        copy=False,
        help='Temporary CSRF state token for OAuth flow'
    )

    # Computed fields for backwards compatibility (decrypt on read)
    x_microsoft_access_token = fields.Char(
        string='Microsoft Access Token',
        compute='_compute_decrypted_tokens',
        inverse='_inverse_access_token',
        store=False,  # Never store plain text in database
        groups='base.group_system',
        copy=False
    )
    x_microsoft_refresh_token = fields.Char(
        string='Microsoft Refresh Token',
        compute='_compute_decrypted_tokens',
        inverse='_inverse_refresh_token',
        store=False,  # Never store plain text in database
        groups='base.group_system',
        copy=False
    )

    @api.depends('x_microsoft_access_token_encrypted', 'x_microsoft_refresh_token_encrypted')
    def _compute_decrypted_tokens(self):
        """Decrypt tokens when reading from database"""
        for user in self:
            user.x_microsoft_access_token = encryption_utils.decrypt_value(
                self.env,
                user.x_microsoft_access_token_encrypted
            ) if user.x_microsoft_access_token_encrypted else False

            user.x_microsoft_refresh_token = encryption_utils.decrypt_value(
                self.env,
                user.x_microsoft_refresh_token_encrypted
            ) if user.x_microsoft_refresh_token_encrypted else False

    def _inverse_access_token(self):
        """Encrypt access token when writing to database"""
        for user in self:
            user.x_microsoft_access_token_encrypted = encryption_utils.encrypt_value(
                self.env,
                user.x_microsoft_access_token
            ) if user.x_microsoft_access_token else False

    def _inverse_refresh_token(self):
        """Encrypt refresh token when writing to database"""
        for user in self:
            user.x_microsoft_refresh_token_encrypted = encryption_utils.encrypt_value(
                self.env,
                user.x_microsoft_refresh_token
            ) if user.x_microsoft_refresh_token else False

    @api.depends('x_microsoft_refresh_token_encrypted')
    def _compute_microsoft_oauth_connected(self):
        """Check if user has a Microsoft OAuth connection (refresh token exists)"""
        for user in self:
            user.x_microsoft_oauth_connected = bool(user.x_microsoft_refresh_token_encrypted)

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

    def action_connect_microsoft(self):
        """
        Start OAuth flow by redirecting directly to Microsoft login.
        No intermediate wizard - goes straight to Microsoft.
        """
        self.ensure_one()

        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        redirect_uri = f"{base_url}/microsoft_oauth/callback"

        graph_client = self.env['microsoft.graph.client']

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

    def write(self, vals):
        """
        Override write to allow users to set their own default mailbox.
        Regular users cannot write to res.users, but they should be able
        to set their own Microsoft default mailbox setting.
        """
        # Check if only updating default mailbox for own record
        if (len(vals) == 1 and
                'x_microsoft_default_mailbox_id' in vals and
                len(self) == 1 and
                self.id == self.env.uid):
            # User is only changing their own default mailbox - allow via sudo
            return super(ResUsers, self.sudo()).write(vals)

        return super().write(vals)
