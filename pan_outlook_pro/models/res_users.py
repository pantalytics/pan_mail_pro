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
