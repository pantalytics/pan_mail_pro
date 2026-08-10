# -*- coding: utf-8 -*-
from odoo import fields, models, api, _
from odoo.exceptions import ValidationError

from . import encryption_utils
from .mail_provider_client import DEFAULT_PROVIDER, PROVIDER_SELECTION, get_provider_client

# Hosts we can fill in for the admin. Keyed on the mail domain, because that is
# what an admin types first. Deliberately tiny: this is a convenience, not a
# provider directory — anything not listed is typed in by hand.
IMAP_PRESETS = {
    'soverin.net': {
        'imap_host': 'imap.soverin.net', 'imap_port': 993, 'imap_security': 'ssl',
        'smtp_host': 'smtp.soverin.net', 'smtp_port': 465, 'smtp_security': 'ssl',
    },
}


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
                       it. Also how an IMAP shared mailbox works, where there is
                       no OAuth at all - just a login.

    Tokens and passwords are Fernet-encrypted at rest, same scheme as res.users
    used before - the ciphertext is interchangeable, which is what lets the
    migration copy rather than re-encrypt.

    Which of the credential fields below matter depends on the provider, and the
    provider is the one that says so: `connected` asks the client through
    `account_is_connected()` rather than assuming everybody has a refresh token.
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
        # Same registry the mailbox's x_provider uses — an account and the
        # mailbox it serves must name the provider identically.
        PROVIDER_SELECTION,
        string='Provider',
        required=True,
        default=DEFAULT_PROVIDER,
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
    connected = fields.Boolean(
        string='Connected',
        compute='_compute_connected',
        store=True,
        help='Whether this account holds credentials its provider can use.'
    )

    # -------------------------------------------------------------------------
    # IMAP / SMTP credentials
    #
    # Only meaningful for provider='imap'. They live here rather than on a
    # separate model for the same reason the tokens do: a caller resolving "the
    # credentials for this address on this provider" must get one record back,
    # whatever authentication that provider happens to use.
    # -------------------------------------------------------------------------
    # Host, port, transport and login share the password's trust boundary, and
    # that is not a tidiness argument. `_smtp()` decrypts the password through
    # sudo and logs in to whatever `smtp_host` says; anyone who can move the
    # host can point it at a server they control and read the password off the
    # wire on the next send. Leaving these open to a mailbox manager turned
    # "may configure mailboxes" into "knows the mailbox password", a group the
    # module explicitly defines as less than system administration. Nothing is
    # lost by matching them up: the password itself was already system-only, so
    # a mailbox manager could never finish an IMAP account anyway.
    username = fields.Char(
        string='Username',
        groups='base.group_system',
        help='Login for the IMAP/SMTP server. Leave empty to use the email address.',
    )
    password_encrypted = fields.Char(
        string='Password (Encrypted)',
        groups='base.group_system',
        copy=False,
        help='Encrypted - do not edit manually',
    )
    password = fields.Char(
        string='Password',
        compute='_compute_decrypted_password',
        inverse='_inverse_password',
        store=False,
        groups='base.group_system',
        copy=False,
    )

    imap_host = fields.Char(
        string='IMAP Server', groups='base.group_system',
        help='e.g. imap.soverin.net')
    imap_port = fields.Integer(
        string='IMAP Port', groups='base.group_system', default=993)
    imap_security = fields.Selection([
        ('ssl', 'SSL/TLS'),
        ('starttls', 'STARTTLS'),
        ('none', 'None'),
    ], string='IMAP Security', groups='base.group_system', default='ssl')
    imap_sent_folder = fields.Char(
        string='Sent Folder',
        groups='base.group_system',
        help='IMAP folder holding sent mail. Leave empty to detect it from the '
             'server\'s \\Sent flag, falling back to "Sent".',
    )

    smtp_host = fields.Char(
        string='SMTP Server', groups='base.group_system',
        help='e.g. smtp.soverin.net')
    smtp_port = fields.Integer(
        string='SMTP Port', groups='base.group_system', default=465)
    smtp_security = fields.Selection([
        ('ssl', 'SSL/TLS'),
        ('starttls', 'STARTTLS'),
        ('none', 'None'),
    ], string='SMTP Security', groups='base.group_system', default='ssl')

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


    def action_test_connection(self):
        """Verify these credentials against the provider, from the account form."""
        self.ensure_one()
        result = get_provider_client(self.env, self.provider).test_connection(self)
        if result.get('success'):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Connection Successful'),
                    'message': _('Connected as %s.') % (result.get('email') or self.email),
                    'type': 'success',
                    'sticky': False,
                },
            }
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Connection Failed'),
                'message': result.get('error') or _('Unknown error'),
                'type': 'danger',
                'sticky': True,
            },
        }

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

    @api.depends('password_encrypted')
    def _compute_decrypted_password(self):
        for account in self:
            account.password = encryption_utils.decrypt_value(
                self.env, account.password_encrypted
            ) if account.password_encrypted else False

    def _inverse_password(self):
        for account in self:
            account.password_encrypted = encryption_utils.encrypt_value(
                self.env, account.password
            ) if account.password else False

    @api.depends('provider', 'refresh_token_encrypted', 'password_encrypted',
                 'imap_host', 'smtp_host', 'email', 'username')
    def _compute_connected(self):
        """What makes an account usable is the provider's call, not ours.

        For OAuth providers it is a refresh token; for IMAP/SMTP it is a host, a
        login and a password. Asking the client keeps that difference in the one
        place allowed to know about it - and keeps every `mailbox.connected`
        check in the module provider-neutral.
        """
        for account in self:
            if not account.provider:
                account.connected = False
                continue
            client = get_provider_client(self.env, account.provider)
            account.connected = client.account_is_connected(account)

    def _imap_login(self):
        """The username an IMAP/SMTP server should be given for this account."""
        self.ensure_one()
        return self.username or self.email

    @api.onchange('email', 'provider')
    def _onchange_email_fills_known_hosts(self):
        """Prefill the servers for hosters we know, on an empty IMAP account.

        Never overwrites what an admin typed: an unknown domain, or a form that
        already has a host in it, is left exactly as it is.

        Server fields are system-only, so anybody else editing this form cannot
        read them, let alone be helped by a prefill. Returning early keeps the
        onchange from touching a field the editing user has no rights to.
        """
        if not self.env.su and not self.env.user.has_group('base.group_system'):
            return
        for account in self:
            if account.provider != 'imap' or account.imap_host or account.smtp_host:
                continue
            domain = (account.email or '').split('@')[-1].lower()
            preset = IMAP_PRESETS.get(domain)
            if preset:
                account.update(preset)

    @api.constrains('user_id', 'email')
    def _check_service_account_has_email(self):
        for account in self:
            if not account.user_id and not account.email:
                raise ValidationError(_(
                    'A service account must have an email address - it has no '
                    'user to borrow one from.'
                ))
