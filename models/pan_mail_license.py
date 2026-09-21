# -*- coding: utf-8 -*-
"""This database's link to a Pantalytics account.

The admin presses **Connect to Pantalytics** on the settings page. Odoo asks
our server for a pairing and opens its page in a new tab, with the code already
in the link. The admin signs in with their Pantalytics account, checks that the
page names this Odoo, and approves; the button back to their Odoo lands on
`/mail_pro/pantalytics/return`, which collects the key. Nobody types a code or
copies a key, and there is still no redirect URI to register per customer:
the way back is an ordinary link to this Odoo, not an OAuth redirect.
**Check Approval** on the settings page does the same collection by hand.

After that, one heartbeat a day: counts out, a signed entitlement back. The
server side and the reasons behind it live in `pantalytics/mail-pro-admin`
(`docs/plans/mail-pro-paid.md`).

**There is one row**, created the first time somebody connects, the same shape
as `pan.mail.provider`: `current()` is the answer.

**Mail Pro works on a connected Odoo instance** (pan_mail_pro#126).
`sync_allowed()` is the one answer: incoming sync and connecting a *new*
mailbox account ask it. Outgoing mail never does, because the module took
over Odoo's own SMTP and stopping sends would hold all of the instance's email
hostage. Reconnecting an existing account is allowed too; it changes nothing
about who pays.

There is no grace period: a trial is something the server hands out, so the
module has one question and one answer. Existing customers are upgraded and
connected in the same session; incoming sync pauses for those minutes and the
per-folder cursor catches up afterwards, so no mail is lost.

**What leaves the database** is `_heartbeat_body()`, and the whole list is in
that one method: the database id, two version strings, how many accounts are
connected, whether sync is healthy, and for the last 24 hours how many mails
were sent and received, the four link coverage counts, per matching rule how
often it decided and how often a person overruled it, and how many
conversations were linked by hand. Counts and rule names. No address, subject,
body or name.

**Usage and billing are read at Pantalytics, not here.** `dashboard_url()` is
the link the settings page offers, and there is no usage screen in Odoo: a
second place to read the number is a second number to keep true, and the one
that decides the invoice is the one our own server counted.

**What the answer carries besides the licence** is the workspace's "Help
improve Mail Pro" switch: `improve`, the `improve_host` our proxy answers on,
and the `replay_sample`. Signed like the rest, so nothing on this side records
anything on an unsigned say-so, and `improve_active()` is the one question the
browser side asks (ARCHITECTURE.md §9.17, docs/plans/analytics-flywheel.md in
mail-pro-admin).

**Trust.** An entitlement is only stored after its Ed25519 signature checks out
against the public key this module ships and its `db_uuid` matches this
database, so a cached answer cannot be forged by editing a response or lifted
from another customer. The key itself is Fernet-encrypted like every other
secret here, which also means a neutralized copy cannot read it: a restored
backup does not phone home and does not carry the licence with it.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import requests

from odoo import _, api, fields, models, release
from odoo.exceptions import AccessError, UserError, ValidationError

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

# Where usage and billing are read. The page that carries the counts this
# heartbeat sends, so the link lands on the number rather than on a home page
# somebody then has to navigate from.
DASHBOARD_PATH = '/instances'

# The server accepts this many rule rows; the ladder has six, so the cap only
# guards against a rule name that is somehow not one of ours.
MAX_RULE_ENTRIES = 20

# A connected instance whose entitlement has not arrived asks again this often
# (from the fetch cron), instead of waiting for the daily heartbeat.
RETRY_MINUTES = 10

# Stripe's statuses that keep a customer entitled, as the server decides them.
# past_due stays entitled while Stripe is still retrying the card.
ENTITLED_STATUSES = ('active', 'trialing', 'past_due')

# "Not on this Odoo instance": the local no to the workspace's yes. A config
# parameter rather than a field on the row, so it survives Disconnect and
# Connect, which is when the row's own answer is thrown away and re-fetched.
IMPROVE_REFUSED_PARAM = 'pan_mail_pro.improve_refused'

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


def _sample(value):
    """A share of sessions, 0 to 1. Anything else reads as 0: the answer said
    yes, the number says how much, and a number we cannot read means none."""
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


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
    # The number the plan is metered on (docs/plans/mail-pro-paid.md): mails
    # per UTC day. Read from the signed answer; nothing enforces it yet (#128).
    daily_send_limit = fields.Integer(readonly=True)
    valid_until = fields.Datetime(readonly=True)
    latest_version = fields.Char(readonly=True)
    message = fields.Char(readonly=True)
    entitlement_json = fields.Text(readonly=True, copy=False)
    signature = fields.Char(readonly=True, copy=False)

    last_check = fields.Datetime(readonly=True, copy=False)
    last_error = fields.Char(readonly=True, copy=False)

    # Help improve Mail Pro, as the workspace decided it at Pantalytics. Read
    # off the signed entitlement and nowhere else; the local checkbox that
    # refuses it lives in ir.config_parameter, because an administrator here
    # may say no and never yes.
    improve = fields.Boolean(readonly=True, copy=False)
    improve_host = fields.Char(readonly=True, copy=False)
    improve_token = fields.Char(readonly=True, copy=False)
    replay_sample = fields.Float(readonly=True, copy=False)

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

        It reads the stored answer rather than calling out, because the gate
        must keep working while our server or the customer's firewall does not.
        """
        self.ensure_one()
        return bool(
            self.status in ENTITLED_STATUSES
            and self.valid_until
            and self.valid_until > fields.Datetime.now()
        )

    @api.model
    def sync_allowed(self):
        """May this instance sync incoming mail and connect new accounts?"""
        link = self.current()
        return bool(link) and link.is_entitled()

    @api.model
    def improve_active(self):
        """May the Inbox in this browser report how it is used and record?

        Four yeses, any no wins: the workspace switched it on, the answer named
        a host and a project to send to, no administrator here refused it, and
        this is not a neutralized copy. The host check matters on its own: an
        older server that says yes without saying where would otherwise leave
        the browser to guess.
        """
        link = self.current()
        if not link or not link.improve or not link.improve_host or not link.improve_token:
            return False
        if self.env['ir.config_parameter'].sudo().get_param(IMPROVE_REFUSED_PARAM):
            return False
        return not database_is_neutralized(self.env)

    @api.model
    def improve_config(self):
        """What the browser needs to report, or False. Read into `session_info`
        by `ir.http`, so the Inbox knows before its first paint and makes no
        call of its own to find out.

        The person is `u:<hmac>`: an HMAC under this database's own
        encryption key over the database id and the user id, twelve hex
        characters. Two users are two ids, a user is the same id tomorrow, and
        nothing we hold turns it back into a person.
        """
        if not self.improve_active():
            return False
        link = self.current()
        return {
            'host': link.improve_host,
            'token': link.improve_token,
            'user': self._improve_user_id(),
            'sample': link.replay_sample,
            'version': self._module_version(),
        }

    @api.model
    def _improve_user_id(self):
        key = encryption_utils.get_encryption_key(self.env)
        digest = hmac.new(
            key, f'{self._db_uuid()}:{self.env.uid}'.encode(), hashlib.sha256,
        ).hexdigest()
        return 'u:' + digest[:12]

    @api.model
    def not_allowed_error(self):
        """Why sync is refused, in the words of the state it is refused in.

        One sentence for every refusal used to say "connect", which is wrong
        advice for six of the seven states it covered: an admin whose key was
        revoked, or whose server cannot reach ours, had already connected.
        """
        link = self.current()
        status = link.status if link else 'not_connected'
        tail = _(' Until then incoming mail is not synced and no new mailbox '
                 'can be connected. Sending keeps working.')
        if status == 'not_connected':
            head = _('Connect this Odoo instance to Pantalytics to use Mail Pro: '
                     'Settings, Mail Pro, Connect to Pantalytics.')
        elif status == 'pending':
            head = _('The connection to Pantalytics is waiting for approval: '
                     'Settings, Mail Pro, Check Approval.')
        elif status == 'invalid':
            head = _('Pantalytics no longer recognises the key of this Odoo '
                     'instance: Settings, Mail Pro, Disconnect, then Connect to '
                     'Pantalytics again.')
        elif status == 'revoked':
            head = _('The connection to Pantalytics was replaced or revoked: '
                     'Settings, Mail Pro, Disconnect, then Connect to Pantalytics '
                     'again.')
        elif status == 'canceled':
            head = _('This Odoo instance has no Mail Pro subscription. See '
                     'Settings, Mail Pro.')
        elif link.last_error:
            head = _('The last check with Pantalytics failed: %s It is retried '
                     'automatically.') % link.last_error
        else:
            head = _('Pantalytics has not confirmed this Odoo instance yet. It is '
                     'asked again automatically; Settings, Mail Pro shows the '
                     'result.')
        return head + tail

    def _check_admin(self):
        """Connecting and disconnecting are an administrator's acts, whoever
        calls: the methods are public on the model and reachable over RPC, and
        the link record is written as sudo."""
        if not self.env.su and not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Only an administrator can change the connection '
                                'to Pantalytics.'))

    # -------------------------------------------------------------------------
    # Pairing
    # -------------------------------------------------------------------------

    @api.model
    def action_connect(self):
        """Ask our server for a code, and show it with the link."""
        self._refuse_when_neutralized()
        self._check_admin()
        link = self.current() or self.sudo().create({})
        if link.is_entitled():
            raise UserError(_('This Odoo instance is already connected to Pantalytics. '
                              'Disconnect it first to connect it again.'))
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
        self._check_admin()
        if self.key_encrypted and self.status in ENTITLED_STATUSES and not self.is_entitled():
            # Connected, but the answer never came (or lapsed): ask again now
            # rather than at the next daily heartbeat.
            self._heartbeat_guarded()
            return False
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
            # Guarded: the server has handed the key over exactly once. An
            # exception here would roll the key back while the server already
            # counts it as collected, and the next poll would say so.
            self._heartbeat_guarded()
            return False
        if status in ('pending', 'too_soon'):
            return self._notify(_('Not approved yet. Approve it on the Pantalytics '
                                  'page first, then check again.'))
        if code >= 500 or not status:
            # Our server, or the proxy in front of it, did not answer properly.
            # That is a bad minute on our side (a deploy restarts it), not a
            # verdict on this code: keep the pairing and say so.
            return self._notify(_('Pantalytics did not answer (HTTP %s). Try again '
                                  'in a minute; the code stays valid.') % code, 'warning')
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
        if not self.env.user.has_group('base.group_system'):
            return
        if self.status == 'pending' and not database_is_neutralized(self.env):
            try:
                self.action_check_approval()
            except UserError as error:
                self.last_error = str(error)

    def action_disconnect(self):
        """Forget the key and the last answer on this database."""
        self.ensure_one()
        self._check_admin()
        self._clear_pairing()
        self.write({
            'status': 'not_connected',
            'key_encrypted': False,
            'plan': False,
            'daily_send_limit': 0,
            'valid_until': False,
            'latest_version': False,
            'message': False,
            'entitlement_json': False,
            'signature': False,
            'last_error': False,
            'improve': False,
            'improve_host': False,
            'improve_token': False,
            'replay_sample': 0.0,
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

    @api.model
    def _retry_if_stuck(self):
        """Connected, key in hand, no usable answer: ask again, at most every
        RETRY_MINUTES. Called from the fetch cron, so a first heartbeat that
        met a bad minute costs ten minutes of sync rather than a day."""
        link = self.current()
        if (not link or not link.key_encrypted
                or link.status not in ENTITLED_STATUSES or link.is_entitled()):
            return
        if link.last_check and fields.Datetime.now() - link.last_check < timedelta(
                minutes=RETRY_MINUTES):
            return
        link._heartbeat_guarded()

    def _heartbeat_guarded(self):
        """A heartbeat that cannot take the caller's transaction down with it."""
        try:
            with self.env.cr.savepoint():
                self._heartbeat()
        except Exception as error:  # noqa: BLE001 - recorded, never raised
            _logger.exception('[License] Heartbeat failed')
            self.write({'last_check': fields.Datetime.now(), 'last_error': str(error)})

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

        if code == 403 and body.get('status') == 'wrong_database':
            # The key was paired for another database uuid: this is a copy
            # (a restore under another name, a staging clone). It gets no
            # entitlement, and it says so instead of looking connected.
            _logger.warning('[License] The key belongs to another Odoo database')
            self.write({
                'status': 'invalid', 'last_check': now,
                'last_error': _('This key was issued to another Odoo database. A copy '
                                'needs its own connection: Settings, Mail Pro, '
                                'Disconnect, then Connect to Pantalytics.'),
                'entitlement_json': False, 'signature': False, 'valid_until': False,
            })
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
            'daily_send_limit': int(payload.get('daily_send_limit') or 0),
            'valid_until': _naive_utc(payload.get('valid_until')),
            'latest_version': payload.get('latest_version') or False,
            'message': payload.get('message') or False,
            'entitlement_json': json.dumps(payload, sort_keys=True),
            'signature': signature,
            'last_check': now,
            'last_error': False,
            'improve': bool(payload.get('improve')),
            'improve_host': (payload.get('improve_host') or '')[:255] or False,
            'improve_token': (payload.get('improve_token') or '')[:128] or False,
            'replay_sample': _sample(payload.get('replay_sample')),
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

        Counts, flags and rule names only.

        **Sent and received are two numbers, not a list of mail.** They are
        what the plan is metered on and what the Odoo instances page draws a
        fortnight of, so the customer and we read the same number. They are
        counted off `mail.message.x_direction`, which is set on every mail
        this module carried and on nothing else: a note, a system log and mail
        from before Mail Pro are all invisible to it. One message is one mail
        here, while the limit is metered per `mail.mail` (one per recipient),
        so a mail to three people counts once in this number and three times
        against the cap -- the throttle keeps its own counter for that
        (mail-pro-admin `docs/plans/mail-pro-paid.md`), and this is the trend,
        not the meter.

        The coverage counts and the rule counts are the two numbers a decision
        does wait on: whether linking gets better per release, and which rule
        earns its place. Both are the last 24 hours, the same slice as the
        counts around them, and both are what the customer reads on their own
        Link Coverage screen and routing log.
        """
        Mailbox = self.env['pan.mail.mailbox'].sudo()
        mailboxes = Mailbox.search_count([])
        since = fields.Datetime.now() - timedelta(hours=24)
        rules = self.env['pan.mail.routing.log'].rule_counts_since(since)
        Message = self.env['mail.message'].sudo()
        return {
            'db_uuid': self._db_uuid(),
            'module_version': self._module_version(),
            'odoo_version': release.major_version,
            'mailboxes_connected': self.env['pan.mail.account'].sudo().search_count(
                [('connected', '=', True)]),
            'mails_sent_24h': Message.search_count(
                [('x_direction', '=', 'outgoing'), ('date', '>=', since)]),
            'mails_received_24h': Message.search_count(
                [('x_direction', '=', 'incoming'), ('date', '>=', since)]),
            'sync_ok': (not Mailbox.search_count([('state', '=', 'error')])
                        if mailboxes else None),
            'coverage': self.env['pan.mail.coverage'].counts_since(since),
            'rules': rules[:MAX_RULE_ENTRIES],
            'corrections': sum(r['corrected'] for r in rules),
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
    def dashboard_url(self):
        """The Pantalytics page that carries this instance's usage and its
        plan. One link, always the same, whether or not this Odoo is connected
        yet: a person who lost their way back needs it most when the status
        line here says nothing."""
        return _server_url() + DASHBOARD_PATH

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
