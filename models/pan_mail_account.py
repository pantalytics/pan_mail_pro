# -*- coding: utf-8 -*-
from odoo import fields, models, api, _
from odoo.exceptions import ValidationError

from . import encryption_utils


class PanMailAccount(models.Model):
    """Credentials for one email address on one provider.

    Deliberately not "a user's Microsoft connection" - that framing is what
    boxed the module into a single provider. An account is credentials for an
    address, and who (if anyone) owns it is a separate question:

    - user_id set   -> a user's own connection. They authorized it; their token
                       sends their mail.
    - user_id null  -> a service account. Nobody's personal mailbox. This is how
                       a Gmail shared mailbox works: sales@company.com is a real
                       Workspace user, authorized once, with no Odoo user behind
                       it. Also how IMAP will work, where there is no OAuth at all.

    Tokens are Fernet-encrypted at rest, same scheme as res.users used before -
    the ciphertext is interchangeable, which is what lets the migration copy
    rather than re-encrypt.
    """
    _name = 'pan.mail.account'
    _description = 'Email Account'
    _rec_name = 'email'
    _order = 'email'

    email = fields.Char(
        string='Email Address',
        required=True,
        index=True,
        help='The address these credentials authenticate.'
    )
    provider = fields.Selection(
        [('microsoft', 'Microsoft 365'), ('google', 'Google Workspace')],
        string='Provider',
        required=True,
        default='microsoft',
    )
    user_id = fields.Many2one(
        'res.users',
        string='Odoo User',
        ondelete='cascade',
        index=True,
        help='The user who owns this connection. Empty for a service account '
             'that belongs to a mailbox rather than a person.',
    )
    active = fields.Boolean(default=True)

    # -------------------------------------------------------------------------
    # Credentials (encrypted at rest)
    # -------------------------------------------------------------------------
    access_token_encrypted = fields.Char(
        string='Access Token (Encrypted)',
        groups='base.group_system',
        copy=False,
        help='Encrypted - do not edit manually'
    )
    refresh_token_encrypted = fields.Char(
        string='Refresh Token (Encrypted)',
        groups='base.group_system',
        copy=False,
        help='Encrypted - do not edit manually'
    )
    token_expiry = fields.Datetime(
        string='Token Expiry',
        groups='base.group_system',
        copy=False,
    )
    oauth_state = fields.Char(
        string='OAuth State',
        groups='base.group_system',
        copy=False,
        help='Temporary CSRF state token for the OAuth flow'
    )

    connected = fields.Boolean(
        string='Connected',
        compute='_compute_connected',
        store=True,
        help='Whether this account has a refresh token and can obtain new access tokens.'
    )

    # Plain-text views onto the encrypted columns. Never stored.
    access_token = fields.Char(
        string='Access Token',
        compute='_compute_decrypted_tokens',
        inverse='_inverse_access_token',
        store=False,
        groups='base.group_system',
        copy=False,
    )
    refresh_token = fields.Char(
        string='Refresh Token',
        compute='_compute_decrypted_tokens',
        inverse='_inverse_refresh_token',
        store=False,
        groups='base.group_system',
        copy=False,
    )

    _unique_user_provider = models.Constraint(
        'UNIQUE(user_id, provider)',
        'A user can only have one account per provider.',
    )

    @api.model
    def _store_tokens(self, provider, user, email, access_token, refresh_token, token_expiry):
        """Upsert the OAuth tokens for one user's account on one provider.

        The direct path for providers built after Phase 2 (Google): the OAuth
        callback writes credentials straight to the account instead of through
        the res.users proxies that exist only for Microsoft's legacy callers.

        refresh_token is written only when present - Google returns it on the
        first consent but not on later re-authorizations, and overwriting it with
        an empty value would disconnect the account.
        """
        account = self.sudo().with_context(active_test=False).search([
            ('user_id', '=', user.id), ('provider', '=', provider),
        ], limit=1)

        vals = {'access_token': access_token, 'token_expiry': token_expiry}
        if refresh_token:
            vals['refresh_token'] = refresh_token

        if account:
            if email and not account.email:
                vals['email'] = email
            account.write(vals)
        else:
            vals.update({'provider': provider, 'user_id': user.id, 'email': email})
            account = self.create(vals)
        return account

    @api.model
    def _for_users(self, users, provider):
        """Map user id -> account, in one query for the whole recordset.

        Returned records are sudo'd. Reading a token is by definition a
        privileged operation, and the callers that need one - the mail queue,
        the incoming cron - run as someone other than the account's owner. The
        access rule lives on the fields that expose these tokens, not here.
        """
        accounts = self.sudo().with_context(active_test=False).search([
            ('user_id', 'in', users.ids), ('provider', '=', provider),
        ])
        return {account.user_id.id: account for account in accounts}

    @api.model
    def _for_user(self, user, provider):
        if not user:
            return self.sudo().browse()
        return self._for_users(user, provider).get(user.id, self.sudo().browse())

    @api.depends('access_token_encrypted', 'refresh_token_encrypted')
    def _compute_decrypted_tokens(self):
        for account in self:
            account.access_token = encryption_utils.decrypt_value(
                self.env, account.access_token_encrypted
            ) if account.access_token_encrypted else False
            account.refresh_token = encryption_utils.decrypt_value(
                self.env, account.refresh_token_encrypted
            ) if account.refresh_token_encrypted else False

    def _inverse_access_token(self):
        for account in self:
            account.access_token_encrypted = encryption_utils.encrypt_value(
                self.env, account.access_token
            ) if account.access_token else False

    def _inverse_refresh_token(self):
        for account in self:
            account.refresh_token_encrypted = encryption_utils.encrypt_value(
                self.env, account.refresh_token
            ) if account.refresh_token else False

    @api.depends('refresh_token_encrypted')
    def _compute_connected(self):
        """A refresh token is what makes an account usable past the next hour."""
        for account in self:
            account.connected = bool(account.refresh_token_encrypted)

    @api.constrains('user_id', 'email')
    def _check_service_account_has_email(self):
        for account in self:
            if not account.user_id and not account.email:
                raise ValidationError(_(
                    'A service account must have an email address - it has no '
                    'user to borrow one from.'
                ))
