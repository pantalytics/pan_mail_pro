# -*- coding: utf-8 -*-
import logging

from odoo import fields, models, api, _
from odoo.exceptions import AccessError, UserError, ValidationError

from . import encryption_utils
from .mail_provider_client import PROVIDER_SELECTION, get_provider_client

_logger = logging.getLogger(__name__)

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
        # Same registry the mailbox's provider uses — an account and the
        # mailbox it serves must name the provider identically.
        PROVIDER_SELECTION,
        string='Provider',
        required=True,
        default=lambda self: self.env['pan.mail.provider'].current_code(),
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
    # The identity behind the credentials
    #
    # What the provider said about this sign-in at consent, kept rather than
    # discarded. The address alone is what the module stored until 19.0.28,
    # and the address is the one thing a directory renames: the stable id is
    # what says "same sign-in", the principal name is what an administrator
    # sees in Entra, the tenant is what refuses a sign-in from the wrong
    # directory before it fails at the first send. All of it is the
    # provider's word, written by the consent callback and never typed.
    # -------------------------------------------------------------------------
    provider_user_id = fields.Char(
        string='Provider id', readonly=True, copy=False,
        help="The provider's own stable id for this sign-in: the object id "
             "in Entra, `sub` on Google. Survives a rename of the address.")
    principal_name = fields.Char(
        string='Sign-in name', readonly=True, copy=False,
        help='The name the sign-in is known by at the provider '
             '(userPrincipalName on Microsoft 365), which is not always the '
             'mail address.')
    display_name = fields.Char(
        string='Name', readonly=True, copy=False,
        help='What the provider calls the person.')
    tenant_id = fields.Char(
        string='Tenant', readonly=True, copy=False,
        help='The directory this sign-in belongs to: the Entra tenant id, or '
             'the Google Workspace domain.')
    granted_scopes = fields.Char(
        string='Granted scopes', readonly=True, copy=False,
        help='The permissions this grant carries, as the provider reported '
             'them at consent. A permission added to the module after this '
             'grant is not in it until the person reconnects.')
    connected_date = fields.Datetime(
        string='Connected on', readonly=True, copy=False,
        help='When this grant was made.')
    verified_date = fields.Datetime(
        string='Verified on', readonly=True, copy=False,
        help='When these credentials last answered the provider.')

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
                    'message': _('Connected as %s.') % self._identity_label(result),
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

    def _identity_label(self, identity):
        """A normalized identity as one string: the name with the address
        behind it where the provider gives a name, the address alone otherwise."""
        email = identity.get('email') or self.email
        name = identity.get('name') or self.display_name
        return f'{name} ({email})' if name and name != email else email

    def has_scope(self, scope):
        """Does this grant carry `scope`, as the provider reported it?

        False when nothing was recorded: a grant from before `granted_scopes`
        existed may well carry the permission, but a probe that assumes so
        and meets a 403 cannot tell consent from rights. The person
        reconnects once and the answer is known from then on.
        """
        self.ensure_one()
        granted = (self.granted_scopes or '').split()
        return scope in granted or any(
            item.rsplit('/', 1)[-1] == scope for item in granted)

    @api.model
    def _store_tokens(self, provider, user, email, access_token, refresh_token, token_expiry,
                      identity=None, scopes=None):
        """Upsert the OAuth tokens for one user's account on one provider.

        The direct path for providers built after Phase 2 (Google): the OAuth
        callback writes credentials straight to the account instead of through
        the res.users proxies that exist only for Microsoft's legacy callers.

        refresh_token is written only when present - Google returns it on the
        first consent but not on later re-authorizations, and overwriting it with
        an empty value would disconnect the account.

        `identity` is the normalized identity the provider reported
        (`identity_shape`), `scopes` the scope line of the grant; both are
        kept on the row beside the tokens, as metadata about the sign-in.
        """
        account = self.sudo().with_context(active_test=False).search([
            ('user_id', '=', user.id), ('provider', '=', provider),
        ], limit=1)

        vals = {'access_token': access_token, 'token_expiry': token_expiry}
        if refresh_token:
            vals['refresh_token'] = refresh_token
        now = fields.Datetime.now()
        vals.update({'connected_date': now, 'verified_date': now})
        if scopes is not None:
            vals['granted_scopes'] = scopes or False
        for key, field_name in (('provider_user_id', 'provider_user_id'),
                                ('principal_name', 'principal_name'),
                                ('name', 'display_name'),
                                ('tenant_id', 'tenant_id')):
            if identity and identity.get(key) is not None:
                vals[field_name] = identity[key]

        if account:
            if email and account.email and email.lower() != account.email.lower():
                if account.connected:
                    # The person consented as somebody else while A still
                    # works. Writing B's tokens onto A's row splits one
                    # identity over two personal mailboxes and sends from A
                    # with B's token. Refuse, and name the way out.
                    raise UserError(_(
                        'This Odoo user is connected as %(current)s. To connect '
                        '%(new)s instead, first press Disconnect under My '
                        'Preferences, Mail Pro.', current=account.email, new=email))
                # Disconnected, and back as another address: the address
                # changed (a rename at the provider, a wrong first consent).
                # The row follows the person; the personal mailbox of the old
                # address retires, so nothing sends from A with B's token.
                self._retire_personal_mailbox(user, account.email)
                vals['email'] = email
            if email and not account.email:
                vals['email'] = email
            account.write(vals)
        else:
            vals.update({'provider': provider, 'user_id': user.id, 'email': email})
            account = self.create(vals)
        return account

    @api.model
    def _retire_personal_mailbox(self, user, email):
        """Archive the user's own personal mailbox for an address they no
        longer sign in with, and forget it as their default."""
        Mailbox = self.env['pan.mail.mailbox'].sudo()
        old = Mailbox.search([
            ('owner_user_id', '=', user.id), ('email', '=ilike', email),
            ('mailbox_type', '=', 'personal'), ('is_notification_mailbox', '=', False),
        ])
        if old:
            old.write({'active': False})
            if user.x_default_mailbox_id in old:
                user.sudo().write({'x_default_mailbox_id': False})
            _logger.info('[OAuth] Retired personal mailbox %s of %s', email, user.login)

    @api.model_create_multi
    def create(self, vals_list):
        """A new account needs a connected Odoo instance (pan_mail_pro#126).

        Only new ones: reconnecting an account that exists is a `write`, and a
        person whose token expired must be able to sign in again whatever the
        instance's standing.
        """
        License = self.env['pan.mail.license']
        if not License.sync_allowed():
            raise UserError(License.not_allowed_error())
        return super().create(vals_list)

    # The two fields that say whose credentials these are.
    IDENTITY_FIELDS = ('user_id', 'email')

    def write(self, vals):
        """Whose account this is, is not a mailbox manager's to change.

        A manager writes every account (`rule_mail_account_manager_all`, and
        the manager row in the ACL), and the only guard on the row is the
        `groups=` on the credential columns. `user_id` and `email` carry no
        such guard, because the account form has to show them -- but moving
        either *is* taking the credentials. A manager who writes a colleague's
        `user_id` to themselves, then makes the colleague's mailbox their own,
        has `_compute_mailbox_type` call it personal and `_is_sendable_by` let
        them send with the colleague's token. So once an account names a
        person or an address, changing either is refused to anyone below
        `base.group_system`, as an AccessError and not as a hidden field: the
        row stays readable and the rest of it stays theirs to configure.

        The superuser is exempt, the way it is from every guard in the module.
        That covers the one legitimate path that moves `email`: the OAuth
        callback through `_store_tokens`, which runs sudo and rewrites the
        address of a *disconnected* account only. Creating is untouched: a
        manager's own account and a service account start out with the right
        values on them, and a row that never had them has nothing to take.
        """
        moved = [name for name in self.IDENTITY_FIELDS if name in vals]
        if moved and not self.env.su and not self.env.user.has_group('base.group_system'):
            for account in self:
                for name in moved:
                    if not account._identity_changes(name, vals[name]):
                        # The value it already holds, or the same address in
                        # another case: not a change, and not a rewrite either.
                        # The stored spelling is what `_service_account` and
                        # the mailbox-type compute match on, exactly.
                        if name == 'email' and len(self) == 1:
                            vals = {k: v for k, v in vals.items() if k != 'email'}
                        continue
                    _logger.warning(
                        '[Mail Pro] User %s (id=%s) tried to change %s of account %s (user %s)',
                        self.env.user.login, self.env.user.id, name,
                        account.email, account.user_id.login or '-',
                    )
                    raise AccessError(_(
                        'Only an administrator may change the user or the address '
                        'of an email account. %(email)s stays with whoever connected it.',
                        email=account.email,
                    ))
        return super().write(vals)

    def _identity_changes(self, name, value):
        """Does writing `value` to `name` point this row at somebody else?

        Only a row that already names a person or an address has an identity
        to take, and writing the value it already holds is not a change.
        """
        self.ensure_one()
        if not (self.user_id or self.email):
            return False
        if name == 'user_id':
            return (value or False) != (self.user_id.id or False)
        return (value or '').strip().lower() != (self.email or '').strip().lower()

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
