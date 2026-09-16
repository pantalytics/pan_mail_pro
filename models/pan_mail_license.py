# -*- coding: utf-8 -*-
"""This database's link to a Pantalytics account.

The admin presses **Connect to Pantalytics** on the settings page. Odoo asks
our server for a pairing and opens its page in a new tab, with the code already
in the link. The admin signs in with their Pantalytics account, checks that the
page names this Odoo, and approves; **Back to Odoo** lands on
`/mail_pro/pantalytics/return`, which collects the key. Nobody types a code or
copies a key, and there is still no redirect URI to register per customer:
the way back is an ordinary link to this Odoo, not an OAuth redirect.
**Check Approval** on the settings page does the same collection by hand.

After that, one heartbeat a day: counts out, a signed entitlement back. The
server side and the reasons behind it live in `pantalytics/mail-pro-admin`
(`docs/plans/mail-pro-paid.md`).

**There is one row**, created the first time somebody connects, the same shape
as `pan.mail.provider`: `current()` is the answer.

**Nothing here restricts anything yet.** `is_entitled()` exists for the gate,
and the gate is deliberately not in this release: every database that runs Mail
Pro today has no key, so a gate now would switch off incoming sync at every
existing customer. It lands with the legacy keys they will be sent first.

**What leaves the database** is `_heartbeat_body()`, and the whole list is in
that one method: the database id, two version strings, how many accounts are
connected and whether sync is healthy. No address, subject, body or name.

**Trust.** An entitlement is only stored after its Ed25519 signature checks out
against the public key this module ships and its `db_uuid` matches this
database, so a cached answer cannot be forged by editing a response or lifted
from another customer. The key itself is Fernet-encrypted like every other
secret here, which also means a neutralized copy cannot read it: a restored
backup does not phone home and does not carry the licence with it.
"""
import base64
import json
import logging
import os
from datetime import datetime, timezone

import requests

from odoo import _, api, fields, models, release
from odoo.exceptions import UserError, ValidationError

from . import encryption_utils
from .neutralization import database_is_neutralized

_logger = logging.getLogger(__name__)

LICENSE_URL = 'https://app.mailpro.pantalytics.com'

# The verifying half of the key app.mailpro.pantalytics.com signs entitlements
# with (generated 2026-09-15 by mail-pro-admin's deploy/bootstrap.sh; the private
# half lives only in that server's deploy/.env). Publishing it is safe: it can
# only check a signature, never make one. Rotating the signing key means
# shipping a module with the new value here.
PUBLIC_KEY = 'ZWLGGpks2iRjFguGsGlhpDl64ekFiDCM7L/O/g3xK/8='

# Deployment-level overrides for testing against a staging server, read from
# the environment like PAN_MAIL_ENCRYPTION_KEY. Never a settings field: a wrong
# value there is a support ticket nobody can diagnose from the screen.
ENV_URL = 'PAN_MAIL_PRO_LICENSE_URL'
ENV_PUBLIC_KEY = 'PAN_MAIL_PRO_LICENSE_PUBLIC_KEY'

TIMEOUT = 15

# Stripe's statuses that keep a customer entitled, as the server decides them.
# past_due stays entitled while Stripe is still retrying the card.
ENTITLED_STATUSES = ('active', 'trialing', 'past_due')

STATUS_SELECTION = [
    ('not_connected', 'Not Connected'),
    ('pending', 'Waiting for Approval'),
    ('active', 'Active'),
    ('trialing', 'Trial'),
    ('past_due', 'Payment Failed'),
    ('canceled', 'No Subscription'),
    ('revoked', 'Revoked'),
    ('invalid', 'Key Refused'),
]


def _server_url():
    return (os.environ.get(ENV_URL) or LICENSE_URL).rstrip('/')


def _public_key():
    return os.environ.get(ENV_PUBLIC_KEY) or PUBLIC_KEY


def canonical_json(payload):
    """The bytes the server signed. Must match `mailpro_admin.signing` exactly."""
    return json.dumps(
        payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
    ).encode('utf-8')


def verify_signature(payload, signature_b64, public_key_b64):
    """True only for a payload our server signed. Any doubt is a no."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not public_key_b64 or not signature_b64:
        return False
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        key.verify(base64.b64decode(signature_b64), canonical_json(payload))
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True


def _naive_utc(iso_value):
    """An ISO timestamp from the server as the naive UTC datetime Odoo stores."""
    if not iso_value:
        return False
    value = datetime.fromisoformat(iso_value)
    if value.tzinfo:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


class PanMailLicense(models.Model):
    _name = 'pan.mail.license'
    _description = 'Pantalytics Account Link'

    status = fields.Selection(STATUS_SELECTION, default='not_connected', required=True)
    key_encrypted = fields.Char(groups='base.group_system', copy=False)

    # The pairing in progress. The code is for a person to read, so it is
    # stored as is; the device token proves the poll comes from this database,
    # so it is a secret like the key.
    user_code = fields.Char(copy=False)
    verify_url = fields.Char(copy=False)
    device_token_encrypted = fields.Char(groups='base.group_system', copy=False)
    pairing_expires_at = fields.Datetime(copy=False)

    # The last entitlement whose signature checked out, and what it said.
    plan = fields.Char(readonly=True)
    seats_allowed = fields.Integer(readonly=True)
    valid_until = fields.Datetime(readonly=True)
    latest_version = fields.Char(readonly=True)
    message = fields.Char(readonly=True)
    entitlement_json = fields.Text(readonly=True, copy=False)
    signature = fields.Char(readonly=True, copy=False)

    last_check = fields.Datetime(readonly=True, copy=False)
    last_error = fields.Char(readonly=True, copy=False)

    # -------------------------------------------------------------------------
    # One row
    # -------------------------------------------------------------------------

    @api.model
    def current(self):
        """The link, or an empty recordset when nobody has connected yet."""
        return self.sudo().search([], limit=1)

    @api.constrains('status')
    def _check_single_row(self):
        if self.search_count([]) > 1:
            raise ValidationError(_('An Odoo instance links to one Pantalytics account.'))

    def is_entitled(self):
        """Does the last verified answer still cover today?

        Read by nothing yet; the gate arrives with the legacy keys. It reads the
        stored answer rather than calling out, because the gate must keep
        working while our server or the customer's firewall does not.
        """
        self.ensure_one()
        return bool(
            self.status in ENTITLED_STATUSES
            and self.valid_until
            and self.valid_until > fields.Datetime.now()
        )

    # -------------------------------------------------------------------------
    # Pairing
    # -------------------------------------------------------------------------

    @api.model
    def action_connect(self):
        """Ask our server for a code, and show it with the link."""
        self._refuse_when_neutralized()
        link = self.current() or self.sudo().create({})
        code, body = link._post('/api/v1/link/start', {
            'db_uuid': self._db_uuid(),
            'odoo_url': self.env['ir.config_parameter'].sudo().get_param('web.base.url', ''),
            'module_version': self._module_version(),
        })
        if code == 429:
            raise UserError(_('Too many connection attempts from this Odoo instance. '
                              'Try again in a few minutes.'))
        if code != 200 or body.get('status') != 'started':
            raise UserError(_('Pantalytics did not accept the request (HTTP %s). '
                              'Try again later.') % code)
        link.write({
            'status': 'pending',
            'user_code': body['user_code'],
            # The link with the code in it, when the server offers one: the
            # button opens it, so nobody types the code.
            'verify_url': body.get('verify_url_complete') or body['verify_url'],
            'device_token_encrypted': encryption_utils.encrypt_value(
                self.env, body['device_token']),
            'pairing_expires_at': _naive_utc(body.get('expires_at')),
            'last_error': False,
        })
        return link

    def action_check_approval(self):
        """Collect the key once the admin has approved in their browser.

        A button rather than a poll loop in the browser: the admin knows when
        they pressed Approve, and one click after it is less code to keep
        working than a timer on a settings page.

        Returns a notification when there is nothing to collect yet, and False
        when something changed, so the settings page re-reads and redraws.
        """
        self.ensure_one()
        self._refuse_when_neutralized()
        token = encryption_utils.decrypt_value(self.env, self.device_token_encrypted)
        if self.status != 'pending' or not token:
            return False

        code, body = self._post('/api/v1/link/poll', {'device_token': token})
        status = body.get('status')
        if status == 'linked' and body.get('key'):
            self.write({
                'key_encrypted': encryption_utils.encrypt_value(self.env, body['key']),
                'status': 'active',
            })
            self._clear_pairing()
            self._heartbeat()
            return False
        if status in ('pending', 'too_soon'):
            return self._notify(_('Not approved yet. Approve it on the Pantalytics '
                                  'page first, then check again.'))
        # expired, collected, unknown: this pairing cannot finish. Start over.
        self._clear_pairing()
        self.status = 'not_connected' if not self.key_encrypted else self.status
        return self._notify(_('This code is no longer valid. Press Connect to '
                              'Pantalytics to get a new one.'), 'warning')

    def collect_on_return(self):
        """The admin came back from the Pantalytics tab: collect the key now.

        Called by the return route the approval page links to. Whatever the
        answer, the settings page they land on says where things stand, so
        nothing here needs to be shown.
        """
        self.ensure_one()
        if self.status == 'pending' and not database_is_neutralized(self.env):
            try:
                self.action_check_approval()
            except UserError as error:
                self.last_error = str(error)

    def action_disconnect(self):
        """Forget the key and the last answer on this database."""
        self.ensure_one()
        self._clear_pairing()
        self.write({
            'status': 'not_connected',
            'key_encrypted': False,
            'plan': False,
            'seats_allowed': 0,
            'valid_until': False,
            'latest_version': False,
            'message': False,
            'entitlement_json': False,
            'signature': False,
            'last_error': False,
        })

    def _clear_pairing(self):
        self.write({
            'user_code': False,
            'verify_url': False,
            'device_token_encrypted': False,
            'pairing_expires_at': False,
        })

    # -------------------------------------------------------------------------
    # Heartbeat
    # -------------------------------------------------------------------------

    @api.model
    def _cron_heartbeat(self):
        link = self.current()
        if link:
            link._heartbeat()

    def _heartbeat(self):
        """Report in, and store the answer if, and only if, it is ours."""
        self.ensure_one()
        if database_is_neutralized(self.env):
            return
        key = encryption_utils.decrypt_value(self.env, self.key_encrypted)
        if not key:
            return

        now = fields.Datetime.now()
        try:
            code, body = self._post(
                '/api/v1/license/heartbeat', self._heartbeat_body(), key=key)
        except UserError as error:
            # Offline keeps the cached answer: valid_until is the grace period.
            self.write({'last_check': now, 'last_error': str(error)})
            return

        if code == 401:
            _logger.warning('[License] Pantalytics refused the key for this database')
            self.write({
                'status': 'invalid', 'last_check': now,
                'last_error': _('Pantalytics no longer recognises this key.'),
                'entitlement_json': False, 'signature': False, 'valid_until': False,
            })
            return
        if code != 200:
            self.write({'last_check': now,
                        'last_error': _('Heartbeat failed (HTTP %s).') % code})
            return

        payload = body.get('entitlement') or {}
        signature = body.get('signature') or ''
        problem = self._untrusted(payload, signature)
        if problem:
            _logger.warning('[License] Ignored an entitlement: %s', problem)
            self.write({'last_check': now, 'last_error': problem})
            return

        status = payload.get('status')
        self.write({
            'status': status if status in dict(STATUS_SELECTION) else 'invalid',
            'plan': payload.get('plan') or False,
            'seats_allowed': int(payload.get('seats_allowed') or 0),
            'valid_until': _naive_utc(payload.get('valid_until')),
            'latest_version': payload.get('latest_version') or False,
            'message': payload.get('message') or False,
            'entitlement_json': json.dumps(payload, sort_keys=True),
            'signature': signature,
            'last_check': now,
            'last_error': False,
        })

    def _untrusted(self, payload, signature):
        """Why this answer may not be stored, or None when it may."""
        if not verify_signature(payload, signature, _public_key()):
            return _('The answer from Pantalytics was not signed with its key.')
        if payload.get('db_uuid') != self._db_uuid():
            return _('The answer from Pantalytics was meant for another Odoo instance.')
        return None

    @api.model
    def _heartbeat_body(self):
        """Everything this database tells Pantalytics. The whole list.

        Counts and flags only. Sent and received counts and error codes are left
        out until there is a dashboard that shows them: a number nobody reads is
        still a number that left the customer's server.
        """
        Mailbox = self.env['pan.mail.mailbox'].sudo()
        mailboxes = Mailbox.search_count([])
        return {
            'db_uuid': self._db_uuid(),
            'module_version': self._module_version(),
            'odoo_version': release.major_version,
            'mailboxes_connected': self.env['pan.mail.account'].sudo().search_count(
                [('connected', '=', True)]),
            'sync_ok': (not Mailbox.search_count([('state', '=', 'error')])
                        if mailboxes else None),
        }

    # -------------------------------------------------------------------------
    # Plumbing
    # -------------------------------------------------------------------------

    def _post(self, path, payload, key=None):
        """POST to our server. Returns (status code, JSON body); raises UserError
        when the server cannot be reached at all."""
        headers = {'Authorization': f'Bearer {key}'} if key else {}
        try:
            response = requests.post(
                _server_url() + path, json=payload, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as error:
            _logger.warning('[License] %s unreachable: %s', path, error)
            raise UserError(_('Could not reach Pantalytics. Check that this server '
                              'can make outgoing HTTPS requests.')) from error
        try:
            body = response.json()
        except ValueError:
            body = {}
        return response.status_code, body if isinstance(body, dict) else {}

    def _refuse_when_neutralized(self):
        if database_is_neutralized(self.env):
            raise UserError(_('This Odoo instance is a neutralized copy. It cannot be '
                              'linked to a Pantalytics account.'))

    @api.model
    def _db_uuid(self):
        return self.env['ir.config_parameter'].sudo().get_param('database.uuid', '')

    @api.model
    def _module_version(self):
        return self.env['ir.module.module'].sudo().search(
            [('name', '=', 'pan_mail_pro')], limit=1).installed_version or ''

    def _notify(self, message, kind='info'):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'message': message, 'type': kind},
        }
