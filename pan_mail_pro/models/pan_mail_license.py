# -*- coding: utf-8 -*-
"""This database's link to a Pantalytics account.

The admin presses **Connect to Pantalytics**, on the settings page or on the
Inbox's gate. Odoo asks our server for a pairing and opens its page in the
same tab, with the code already in the link. The admin signs in with their
Pantalytics account, checks that the page names this Odoo, and approves; our
server then sends the browser to `/mail_pro/pantalytics/return`, which
collects the key and lands where Connect was pressed. Nobody types a code or
copies a key, and there is still no redirect URI to register per customer:
the way back is an ordinary link to this Odoo, not an OAuth redirect.
**Check approval** on the settings page does the same collection by hand.
Collecting the key also records who did it: the Pantalytics account that
approved, when the poll answer names it (`account_email`), the Odoo user who
pressed the button, and the time. The account page behind step 1 of the
settings checklist (`action_open`) shows it, so a second administrator can
see whose workspace this Odoo is in.

After that, one heartbeat a day: counts out, a signed entitlement back. The
server side runs on the Pantalytics platform (`pantalytics/odoo-mcp-pro-admin`,
`mcp_server_odoo_admin/apps/`); the reasons behind it are in
`pantalytics/mail-pro-admin` (`docs/plans/mail-pro-paid.md`).

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
that one method: the database id, two version strings, where it runs (one of
`sh`, `cloudpepper`, `self_hosted`), how many accounts are connected, whether
sync is healthy, which of the three setup steps are answered, and for the last
24 hours how many mails were sent and received, the
four link coverage counts, per matching rule how often it decided and how often
a person overruled it, and how many conversations were linked by hand. Counts,
flags and rule names. No address, subject, body or name.

**The setup answers do not wait for tomorrow.** Pantalytics draws them as the
Get started line the customer is standing in front of, so a step answered here
pushes a heartbeat within the minute (`_report_setup_if_changed`, from the
fetch cron). Daily is right for counts and wrong for a checklist.

**Usage and billing are read at Pantalytics, not here.** `dashboard_url()` is
the button the account page offers, and there is no usage screen in Odoo: a
second place to read the number is a second number to keep true, and the one
that decides the invoice is the one our own server counted.

**What the answer carries besides the licence** is the workspace's "Help
improve Mail Pro" switch: `improve`, the `improve_host` our proxy answers on,
and the `replay_sample`. Signed like the rest, so nothing on this side records
anything on an unsigned say-so, and `improve_active()` is the one question the
browser side asks (ARCHITECTURE.md §9.17, docs/plans/analytics-flywheel.md in
mail-pro-admin).

**The month's mails ride the answer too** (pan_mail_pro#363):
`mails_included`, `mails_used_month` and `card`, counted at Pantalytics from
these heartbeats. Over the included mails with no card on the workspace,
`incoming_paused()` says yes and incoming sync waits with its own reason, the
way `sync_allowed()` stops it on an unconnected Odoo; sending never waits.
Read off `entitlement_json` (`mail_usage()`), so an older platform that sends
none of the three means no limit.

**Trust.** An entitlement is only stored after its Ed25519 signature checks out
against the public key this module ships and its `db_uuid` matches this
database, so a cached answer cannot be forged by editing a response or lifted
from another customer. The key itself is Fernet-encrypted like every other
secret here.

**A copy connects too** (pan_mail_pro#384). On a copy (`neutralization.
database_is_copy`) Connect, Check approval and the heartbeat run as anywhere
else, and on a neutralized database or an Odoo.sh staging or dev branch the
start call and every heartbeat carry `staging: true` (what the database
sees, never an echo of the answer). The
platform asks on its approval page which production this is a copy of, links
it as "staging of" that one, and answers with `staging: true` in the signed
entitlement, no monthly mails and no bill. That answer keeps the copy a copy
(`database_is_copy` reads it), so connecting one never turns on a provider.
The key and the device token are read past the decryption gate
(`encryption_utils.decrypt_link_secret`): they open the way to Pantalytics
and to nothing else. A production key restored onto a copy whose database id
changed meets `wrong_database` at its first heartbeat, and Connect again on
the copy pairs it as staging. Signing a mailbox in still stops on a copy
(`microsoft_start`), because that is a credential for a live mailbox.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, time, timedelta, timezone

import requests

from odoo import _, api, fields, models, release, tools
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from . import encryption_utils
from .hosting import hosting
from .neutralization import database_is_copy, database_sees_a_copy
from .pan_mail_error import MAX_CODES_REPORTED
from .res_users import _referer_path

_logger = logging.getLogger(__name__)

LICENSE_URL = 'https://mcp.pantalytics.com'

# The verifying half of the key Pantalytics signs entitlements with (generated
# 2026-09-15 by mail-pro-admin's deploy/bootstrap.sh; the platform at
# mcp.pantalytics.com signs with the same private half, which lives only in
# its server's environment). The old host, app.mailpro.pantalytics.com, stays
# an alias of the platform, so releases that still call it keep working. Publishing it is safe: it can
# only check a signature, never make one. Rotating the signing key means
# shipping a module with the new value here.
PUBLIC_KEY = 'ZWLGGpks2iRjFguGsGlhpDl64ekFiDCM7L/O/g3xK/8='

# Deployment-level overrides for testing against a staging server, read from
# the environment like PAN_MAIL_ENCRYPTION_KEY. Never a settings field: a wrong
# value there is a support ticket nobody can diagnose from the screen.
ENV_URL = 'PAN_MAIL_PRO_LICENSE_URL'
ENV_PUBLIC_KEY = 'PAN_MAIL_PRO_LICENSE_PUBLIC_KEY'

TIMEOUT = 15

# Where a Microsoft sign-in through the Pantalytics app starts (pantalytics/
# odoo-mcp-pro-admin #574): the platform issues the state, names the app and
# its redirect URI, and later passes Microsoft's answer back to this Odoo.
MS_START_PATH = '/api/v1/mail-pro/microsoft/start'

# Where usage and billing are read: the workspace's Mail Pro page on the
# platform, which carries the counts this heartbeat sends, so the link lands on
# the number rather than on a home page somebody then has to navigate from.
DASHBOARD_PATH = '/admin/mail-pro'

# The server accepts this many rule rows; the ladder has six, so the cap only
# guards against a rule name that is somehow not one of ours.
MAX_RULE_ENTRIES = 20

# The floor under a setup push. It is the server's own minimum interval: a
# second heartbeat inside a minute is refused there, so asking sooner buys
# nothing and the cron is back in a minute anyway.
SETUP_PUSH_SECONDS = 60

# A connected instance whose entitlement has not arrived asks again this often
# (from the fetch cron), instead of waiting for the daily heartbeat.
RETRY_MINUTES = 10

# The UTC days every heartbeat restates, today included (odoo-mcp-pro-admin
# docs/plans/mail-pro-usage-count.md). The platform sets each day it is sent
# rather than adding to it, so a heartbeat after a week's outage still closes
# the days it missed. The platform refuses more.
MAIL_DAYS = 7

# Stripe's statuses that keep a customer entitled, as the server decides them.
# past_due stays entitled while Stripe is still retrying the card.
ENTITLED_STATUSES = ('active', 'trialing', 'past_due')

# The key itself is gone on the server (refused, or replaced by a newer
# pairing): the only way back is a new pairing. Settings shows Connect again.
DEAD_STATUSES = ('invalid', 'revoked')

# The statuses a new pairing is the fix for: never connected, a pairing nobody
# finished, a dead key. Anything else holds a key whose answer said no (no
# plan, a lapsed answer), and connecting again does not change that answer.
PAIRING_STATUSES = ('not_connected', 'pending') + DEAD_STATUSES

# Where Connect was pressed, for the way back from the approval page to land
# on (#380). The platform sends every approval to one fixed path on this
# Odoo's address (`/mail_pro/pantalytics/return`) and carries nothing else
# back, so the place lives in the session of the browser that pressed the
# button, the way a Microsoft sign-in's does (#370).
CONNECT_BACK_KEY = 'pan_mail_pantalytics_back'

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
    """The bytes the server signed. Must match the platform's `apps/signing.py` exactly."""
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


def _count(value):
    """A count from the signed answer. Anything that is not a whole number of
    zero or more reads as 0, which for `mails_included` means no limit."""
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def setup_signature(answers):
    """The answered setup steps as one string, to compare two heartbeats by."""
    return ','.join(step for step, done in sorted(answers.items()) if done)


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

    # Who connected, and when: written the moment the key is collected, so
    # the account page can say "connected by rutger@pantalytics.com on ..."
    # a year later. `connected_account` is the Pantalytics login that pressed
    # Approve, as the poll answer names it (`account_email`); it is the person
    # Pantalytics knows, which the Odoo user who pressed Connect need not be
    # (a consultant connecting a customer's Odoo is logged in there as the
    # customer's admin). `connected_user_id` is that Odoo user, the fallback
    # when an older server does not name the account.
    connected_account = fields.Char(readonly=True, copy=False)
    connected_user_id = fields.Many2one('res.users', readonly=True, copy=False,
                                        ondelete='set null')
    connected_on = fields.Datetime(readonly=True, copy=False)
    # `connected_by()` as a field, for the account page (`views/
    # pan_mail_license_views.xml`): one label, whichever of the two the
    # server named.
    connected_by_name = fields.Char(string='Connected by', compute='_compute_connected_by_name')
    # What the account page acts on, now that step 1 of the checklist only
    # reports it (#382): incoming waiting on a card, and whether Check again
    # has a question to ask (the same test `action_check_approval` makes).
    mail_paused = fields.Boolean(compute='_compute_attention')
    check_again = fields.Boolean(compute='_compute_attention')

    # Which setup steps the last heartbeat carried, as `setup_signature()`
    # writes them. Compared, never read for its own sake: it is how the module
    # knows a step has been answered since it last said so.
    setup_reported = fields.Char(readonly=True, copy=False)
    # The error codes of the last 24 hours as the last heartbeat carried them
    # (`pan.mail.error.signature_since`), for the same comparison: a code not
    # in it is a kind of failure Pantalytics has not heard of yet.
    errors_reported = fields.Char(readonly=True, copy=False)

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

    def connected_by(self):
        """Who connected this Odoo, as one string for the account page: the
        Pantalytics account when the server named it, else the Odoo user."""
        self.ensure_one()
        return self.connected_account or self.connected_user_id.name or ''

    @api.depends('connected_account', 'connected_user_id')
    def _compute_connected_by_name(self):
        for link in self:
            link.connected_by_name = link.connected_by()

    def _compute_attention(self):
        for link in self:
            paused = link.incoming_paused()
            link.mail_paused = paused
            link.check_again = bool(
                link.sudo().key_encrypted and link.status in ENTITLED_STATUSES
                and (not link.is_entitled() or paused))

    @api.depends('status')
    def _compute_display_name(self):
        # The breadcrumb above the account page; the row has no name of its
        # own and "pan.mail.license,1" is not one.
        for link in self:
            link.display_name = _('Pantalytics account')

    # -------------------------------------------------------------------------
    # The account page
    # -------------------------------------------------------------------------

    def action_open(self):
        """The row as a page: where the arrow on step 1 of the settings
        checklist lands. The line says "Connected"; who connected it, when,
        what the last answer said, usage and billing and the way out are
        here, the same way the other three steps keep their detail on the
        table the arrow opens."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Pantalytics account'),
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_open_dashboard(self):
        return {'type': 'ir.actions.act_url', 'url': self.dashboard_url(), 'target': 'new'}

    @api.model
    def settings_action(self):
        """Settings, Mail Pro: where Disconnect sends you back to, since the
        row it leaves behind has nothing on it but the Connect button there."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Settings'),
            'res_model': 'res.config.settings',
            'view_mode': 'form',
            'target': 'inline',
            'context': {'module': 'pan_mail_pro', 'bin_size': False},
        }

    @api.model
    def sync_allowed(self):
        """May this instance sync incoming mail and connect new accounts?

        Asked on the model it finds the link; asked on the link itself
        (`current().sync_allowed()`) it answers for that row without looking
        it up again. `ir.http` asks three questions of one row on every page
        load, and this is how it pays for one search rather than four. The
        row rather than a keyword because the test suite replaces this
        method with a `(self)`-only yes (tests/connected.py), and a keyword
        it does not know would end every test in a TypeError.
        """
        link = self or self.current()
        return bool(link) and link.is_entitled()

    # -------------------------------------------------------------------------
    # The month's mails (odoo-mcp-pro-admin docs/plans/mail-pro-billing.md)
    # -------------------------------------------------------------------------

    def mail_usage(self):
        """The month's mails as the last signed answer counted them.

        Three keys, read off `entitlement_json` rather than stored in columns
        of their own: the verified payload is already on the row, and a new
        stored field breaks every database that runs the code before `-u`
        (Cloudpepper, #280). `mails_included` 0 means no limit, which is also
        what an answer from an older platform without these keys means, and
        what a value we cannot read means: the platform is what bills, so a
        doubt here never stops somebody's mail.
        """
        self.ensure_one()
        try:
            payload = json.loads(self.entitlement_json or '{}')
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return {
            'mails_included': _count(payload.get('mails_included')),
            'mails_used_month': _count(payload.get('mails_used_month')),
            'card': payload.get('card') is True,
        }

    @api.model
    def incoming_paused(self):
        """Has this instance used the month's included mails, with no card on
        the workspace? Then incoming sync waits; sending never does.

        Asked on the model or on the row, like `sync_allowed`. It says nothing
        about the connection: a lapsed answer is `sync_allowed`'s no, asked
        first, and this one only adds the reason the platform gave.
        """
        link = self or self.current()
        if not link or not link.entitlement_json:
            return False
        usage = link.mail_usage()
        return bool(usage['mails_included']
                    and usage['mails_used_month'] >= usage['mails_included']
                    and not usage['card'])

    @api.model
    def incoming_paused_message(self):
        """The sentence step 1 and the mailboxes show while incoming waits."""
        link = self.current()
        included = link.mail_usage()['mails_included'] if link else 0
        return _('%s mails this month. Add a card to keep receiving.') % included

    @api.model
    def incoming_paused_error(self):
        """Why incoming sync waits, in the shape of `not_allowed_error`."""
        return self.incoming_paused_message() + _(
            ' Until then incoming mail is not synced: Settings, Mail Pro. '
            'Sending keeps working.')

    @api.model
    def improve_active(self, link=None):
        """May the Inbox in this browser report how it is used and record?

        Four yeses, any no wins: the workspace switched it on, the answer named
        a host and a project to send to, no administrator here refused it, and
        this is not a copy. The host check matters on its own: an
        older server that says yes without saying where would otherwise leave
        the browser to guess.

        `link` is the row when the caller has it already (`ir.http`, once per
        page load); left out, it is looked up. `None` means not looked up:
        an empty recordset is an answer, and it is "no".
        """
        link = self.current() if link is None else link
        if not link or not link.improve or not link.improve_host or not link.improve_token:
            return False
        if self.env['ir.config_parameter'].sudo().get_param(IMPROVE_REFUSED_PARAM):
            return False
        return not database_is_copy(self.env)

    @api.model
    def improve_config(self, link=None):
        """What the browser needs to report, or False. Read into `session_info`
        by `ir.http`, so the Inbox knows before its first paint and makes no
        call of its own to find out.

        The person is `u:<hmac>`: an HMAC under this database's own
        encryption key over the database id and the user id, twelve hex
        characters. Two users are two ids, a user is the same id tomorrow, and
        nothing we hold turns it back into a person.

        `link` as in `improve_active`: the row when the caller holds it.
        """
        link = self.current() if link is None else link
        if not self.improve_active(link=link):
            return False
        user = self._improve_user_id()
        if not user:
            return False
        return {
            'host': link.improve_host,
            'token': link.improve_token,
            'user': user,
            'sample': link.replay_sample,
            'version': self._module_version(),
        }

    @api.model
    def _improve_user_id(self):
        """The pseudonym, or None while the database holds no key.

        Read on every page load inside `get_session_info`, which 19.0 serves
        on a read-only cursor, so it never mints the key: that write logged
        "cannot execute INSERT in a read-only transaction" on every fresh
        database. No key costs nothing real. Connecting to Pantalytics
        encrypts the device token, so a link that can say yes to improving
        already has one; without it there is nothing stable to hash with,
        and the session reports nothing rather than a pseudonym that changes.
        """
        key = encryption_utils.get_encryption_key(self.env, generate=False)
        if key is None:
            return None
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
                     'Settings, Mail Pro, Check approval.')
        elif status == 'invalid':
            head = _('Pantalytics no longer recognises the key of this Odoo '
                     'instance: Settings, Mail Pro, Connect again.')
        elif status == 'revoked':
            head = _('The connection to Pantalytics was replaced or revoked: '
                     'Settings, Mail Pro, Connect again.')
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
        """Ask our server for a code, and show it with the link.

        On a copy the call says `staging: true`, and the approval page asks
        which production it is a copy of (#384). Only then: a production's
        start call is the one it always was, so a platform that never heard
        of the key would see nothing new from a production."""
        self._check_admin()
        link = self.current() or self.sudo().create({})
        if link.is_entitled():
            raise UserError(_('This Odoo instance is already connected to Pantalytics. '
                              'Disconnect it first to connect it again.'))
        if link.status in DEAD_STATUSES:
            # The key is dead on the server: Connect again is the whole fix,
            # so it forgets the old key here rather than asking for a
            # Disconnect first. Mailboxes and settings are not touched.
            link.action_disconnect()
        start = {
            'db_uuid': self._db_uuid(),
            'odoo_url': self.env['ir.config_parameter'].sudo().get_param('web.base.url', ''),
            'module_version': self._module_version(),
        }
        if database_sees_a_copy(self.env):
            start['staging'] = True
        code, body = link._post('/api/v1/link/start', start)
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
        self._remember_back()
        return link

    @api.model
    def action_connect_open(self):
        """Connect, then the approval page in this tab: Settings' button and
        the Inbox's gate both press this one.

        The same tab, because a new one opened after this call answers is no
        longer the click, and Brave and Safari block it. Approve on that page
        sends the browser back to `/mail_pro/pantalytics/return`, which
        collects the key and lands where Connect was pressed, so there is
        nothing left to press afterwards."""
        link = self.action_connect()
        return {'type': 'ir.actions.act_url', 'url': link.verify_url, 'target': 'self'}

    def _remember_back(self):
        """Keep where Connect was pressed, for the return route to land on.

        The Inbox's gate says so in the context (`pan_mail_back`); Settings'
        button is an RPC whose Referer is Settings. Checked on the way out
        (`_safe_back`), never trusted here."""
        if not request:
            return
        back = self.env.context.get('pan_mail_back')
        if back is None:
            back = _referer_path()
        request.session[CONNECT_BACK_KEY] = back or False

    @api.model
    def inbox_gate(self):
        """What the Inbox's gate needs, when the page load said not connected.

        `connected`: whether the Inbox opens. An administrator whose pairing
        is still pending gets the key collected here first, the same
        collection the return route makes: whoever approved and reached the
        Inbox by another way than the link back finds it open, with nothing
        to press. `pairs`: whether Connect, a new pairing, is the fix. On a
        link holding a key the answer said no to, it is not, and the gate's
        button opens Settings, which says what is."""
        link = self.current()
        if link:
            link.collect_on_return()
        return {
            'connected': bool(link) and link.sync_allowed(),
            'pairs': not link or link.status in PAIRING_STATUSES,
        }

    def action_check_approval(self):
        """Collect the key once the admin has approved in their browser.

        A button rather than a poll loop in the browser: the admin knows when
        they pressed Approve, and one click after it is less code to keep
        working than a timer on a settings page.

        Returns a notification when there is nothing to collect yet, and False
        when something changed, so the settings page re-reads and redraws.
        """
        self.ensure_one()
        self._check_admin()
        if self.key_encrypted and self.status in ENTITLED_STATUSES and (
                not self.is_entitled() or self.incoming_paused()):
            # Connected, but the answer never came (or lapsed), or it paused
            # incoming over the month's mails: ask again now rather than at
            # the next daily heartbeat.
            self._heartbeat_guarded()
            return False
        token = encryption_utils.decrypt_link_secret(self.env, self.device_token_encrypted)
        if self.status != 'pending' or not token:
            return False

        code, body = self._post('/api/v1/link/poll', {'device_token': token})
        status = body.get('status')
        if status == 'linked' and body.get('key'):
            self.write({
                'key_encrypted': encryption_utils.encrypt_value(self.env, body['key']),
                'status': 'active',
                'connected_account': (body.get('account_email') or '')[:255] or False,
                'connected_user_id': self.env.user.id,
                'connected_on': fields.Datetime.now(),
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
        if self.status == 'pending':
            try:
                self.action_check_approval()
            except UserError as error:
                self.last_error = str(error)

    def action_disconnect(self):
        """Forget the key and the last answer on this database.

        Returns the settings page: pressed on the account page, the row left
        behind is a page with nothing on it, and the Connect button is there.
        The settings page's own Disconnect ignores the return value, so it
        redraws in place as before.
        """
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
            'setup_reported': False,
            'connected_account': False,
            'connected_user_id': False,
            'connected_on': False,
            'improve': False,
            'improve_host': False,
            'improve_token': False,
            'replay_sample': 0.0,
        })
        return self.settings_action()

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
        # Guarded, like every other caller: a heartbeat that raises would
        # otherwise take the cron's transaction with it, and with it the
        # `setup_reported` / `errors_reported` it had just written, so the
        # fetch cron would push the same report again a minute later.
        link = self.current()
        if link:
            link._heartbeat_guarded()
        # The net under `pan.mail.account`'s own call: an own app nobody
        # signs in through any more goes, once a day at the latest (#369).
        self.env['pan.mail.provider']._forget_unused_own_registration()

    @api.model
    def _retry_if_stuck(self):
        """Connected, key in hand, no usable answer: ask again, at most every
        RETRY_MINUTES. Called from the fetch cron, so a first heartbeat that
        met a bad minute costs ten minutes of sync rather than a day.

        Incoming paused over the month's mails asks again on the same clock:
        a card added at Pantalytics, or the 1st of the month, lifts it on the
        platform, and only an answer brings that here."""
        link = self.current()
        if (not link or not link.key_encrypted
                or link.status not in ENTITLED_STATUSES
                or (link.is_entitled() and not link.incoming_paused())):
            return
        if link.last_check and fields.Datetime.now() - link.last_check < timedelta(
                minutes=RETRY_MINUTES):
            return
        link._heartbeat_guarded()

    @api.model
    def _report_setup_if_changed(self):
        """Report in when a setup step has been answered since the last time.

        The three answers ride the heartbeat so Pantalytics can draw the Get
        started line, and that line is read while somebody is still walking
        it: a step ticked in Odoo has to be ticked there within the minute.
        Called from the fetch cron, which runs every minute and keeps running
        during setup, when incoming sync does not.

        `setup_reported` is written by the heartbeat itself, on the way out
        rather than on a good answer: one attempt per change is the contract,
        so a server that cannot be reached costs the line a day of staleness,
        which the daily heartbeat corrects, rather than a heartbeat a minute
        for as long as it stays down.
        """
        link = self.current()
        if not link or not link.key_encrypted:
            return
        signature = setup_signature(self.env['pan.mail.setup'].answers())
        if signature == (link.setup_reported or ''):
            return
        if link.last_check and fields.Datetime.now() - link.last_check < timedelta(
                seconds=SETUP_PUSH_SECONDS):
            return
        link._heartbeat_guarded()

    @api.model
    def _report_errors_if_new(self):
        """Report in when a kind of failure appears that the last day had not.

        Same shape as `_report_setup_if_changed`: the fetch cron asks every
        minute, the signature is the set of error codes of the last 24 hours,
        and it is stored on the way out so an unreachable server costs one
        attempt, not one a minute. A code ageing out changes the set too, so
        the bound is a handful of heartbeats a day, never one per failure: a
        mailbox failing a hundred times today is one code, reported once.
        """
        link = self.current()
        if not link or not link.key_encrypted:
            return
        since = fields.Datetime.now() - timedelta(hours=24)
        signature = self.env['pan.mail.error'].signature_since(since)
        if signature == (link.errors_reported or ''):
            return
        if link.last_check and fields.Datetime.now() - link.last_check < timedelta(
                seconds=SETUP_PUSH_SECONDS):
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
            self.env['pan.mail.error']._record('license.heartbeat_failed', error)

    def _heartbeat(self):
        """Report in, and store the answer if, and only if, it is ours.

        A copy reports in as well, and says so (`staging` in the body, #384):
        its key is read past the decryption gate, which keeps every mailbox
        credential shut on it."""
        self.ensure_one()
        key = encryption_utils.decrypt_link_secret(self.env, self.key_encrypted)
        if not key:
            return

        now = fields.Datetime.now()
        report = self._heartbeat_body()
        # What this one is about to report, recorded before the answer comes
        # back rather than after: `_report_setup_if_changed` reads it to know
        # whether the setup answers have moved since we last said so, and an
        # attempt that never arrives must not turn into one a minute.
        self.setup_reported = setup_signature(report['setup'])
        self.errors_reported = ','.join(sorted(e['code'] for e in report['errors']))
        try:
            code, body = self._post(
                '/api/v1/license/heartbeat', report, key=key)
        except UserError as error:
            # Offline keeps the cached answer: valid_until is the grace period.
            self.write({'last_check': now, 'last_error': str(error)})
            # A warning, not an error: nothing is lost yet. It cannot reach us
            # today by definition; the next heartbeat that does carries it.
            self.env['pan.mail.error']._record(
                'license.heartbeat_failed', error, level='warning')
            return

        # Every refusal from here on is a row in the error ledger as well as
        # `last_error`: the settings page shows the sentence to whoever opens
        # it, the ledger is what the next heartbeat carries and what the
        # Errors screen groups, so a key refused at a hundred customers on the
        # morning of a release shows up as one code, that morning.
        Error = self.env['pan.mail.error']
        if code == 403 and body.get('status') == 'wrong_database':
            # The key was paired for another database uuid: this is a copy
            # (a restore under another name, a staging clone). It gets no
            # entitlement, and it says so instead of looking connected.
            _logger.warning('[License] The key belongs to another Odoo database')
            reason = _('This key was issued to another Odoo database. A copy '
                       'needs its own connection: Settings, Mail Pro, '
                       'Connect again.')
            self.write({
                'status': 'invalid', 'last_check': now, 'last_error': reason,
                'entitlement_json': False, 'signature': False, 'valid_until': False,
            })
            Error._record('license.heartbeat_failed', detail=reason)
            return
        if code == 401:
            _logger.warning('[License] Pantalytics refused the key for this database')
            reason = _('Pantalytics no longer recognises this key.')
            self.write({
                'status': 'invalid', 'last_check': now, 'last_error': reason,
                'entitlement_json': False, 'signature': False, 'valid_until': False,
            })
            Error._record('license.heartbeat_failed', detail=reason)
            return
        if code != 200:
            reason = _('Heartbeat failed (HTTP %s).') % code
            self.write({'last_check': now, 'last_error': reason})
            # A 5xx is a bad minute on our side, retried in ten; anything
            # else is an answer this module did not expect and should hear of.
            Error._record('license.heartbeat_failed', detail=reason,
                          level='warning' if code >= 500 else 'error')
            return

        payload = body.get('entitlement') or {}
        signature = body.get('signature') or ''
        problem = self._untrusted(payload, signature)
        if problem:
            _logger.warning('[License] Ignored an entitlement: %s', problem)
            self.write({'last_check': now, 'last_error': problem})
            Error._record('license.heartbeat_failed', detail=problem)
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
        what the plan is metered on and what the Mail Pro page draws a
        fortnight of, so the customer and we read the same number. They are
        counted off `mail.message.x_direction`, which is set on every mail
        this module carried and on nothing else: a note, a system log and mail
        from before Mail Pro are all invisible to it. `_mail_counts_since`
        says which day a mail belongs to and why a first import is left out.
        One message is one mail
        here, while the limit is metered per `mail.mail` (one per recipient),
        so a mail to three people counts once in this number and three times
        against the cap -- the throttle keeps its own counter for that
        (mail-pro-admin `docs/plans/mail-pro-paid.md`), and this is the trend,
        not the meter.

        **The setup answers are three booleans, not a report.** They say which
        of `pan.mail.setup`'s three steps this database has answered, so the
        Get started line at Pantalytics can tick what is done instead of
        asking the customer to tell it twice.

        **`hosting` is one word from a fixed list** (odoo-mcp-pro-admin#598):
        `sh` when Odoo.sh set `ODOO_STAGE`, `cloudpepper` when the config
        file is Cloudpepper's `/var/odoo/<name>/odoo.conf`, `self_hosted`
        otherwise. The platform stores it as the instance's hosting with the
        highest trust, because only the app stands on the machine. Never a
        host name, a path or the stage itself.

        **`staging` is one boolean, and only a copy sends it** (#384): `true`
        on a database Odoo neutralized or Odoo.sh runs as a staging or dev
        branch, so the platform counts none of its
        mails and bills nothing. It says what this database sees, never what
        the last answer said: echoing a staging answer back would keep a
        database staging for as long as it asked, after the platform had
        moved it. A production leaves the key out rather than saying `false`,
        and its heartbeat is the one it sent before the key existed.

        The coverage counts and the rule counts are the two numbers a decision
        does wait on: whether linking gets better per release, and which rule
        earns its place. Both are the last 24 hours, the same slice as the
        counts around them, and both are what the customer reads on their own
        Link Coverage screen and routing log.
        """
        Mailbox = self.env['pan.mail.mailbox'].sudo()
        mailboxes = Mailbox.search_count([])
        since = fields.Datetime.now() - timedelta(hours=24)
        # A mailbox whose cron stopped getting through (a worker killed for its
        # memory, a cron Odoo switched off) is `active` and reads healthy by
        # state alone. The form already says it is stale; this says it to us.
        stale = len(Mailbox.search([('state', '=', 'active')]).filtered(
            lambda mailbox: mailbox._sync_is_stale()))
        errors = self.env['pan.mail.error'].codes_since(since)
        if stale:
            errors = ([{'code': 'incoming.sync_stale', 'count': stale}]
                      + errors)[:MAX_CODES_REPORTED]
        rules = self.env['pan.mail.routing.log'].rule_counts_since(since)
        mails = self._mail_counts_since(since)
        body = {
            'db_uuid': self._db_uuid(),
            'module_version': self._module_version(),
            'odoo_version': release.major_version,
            'mailboxes_connected': self.env['pan.mail.account'].sudo().search_count(
                [('connected', '=', True)]),
            'mails_sent_24h': mails['outgoing'],
            'mails_received_24h': mails['incoming'],
            # The meter (#599): whole UTC days, which a second heartbeat on
            # the same day restates. The two 24h numbers above stay for a
            # platform from before them.
            'mails_by_day': self._mails_by_day(),
            'sync_ok': (not stale and not Mailbox.search_count([('state', '=', 'error')])
                        if mailboxes else None),
            # The three setup steps as `pan.mail.setup` answers them, which is
            # the same answer the checklist on the settings page draws. Three
            # booleans about this database's configuration; a step name can
            # carry nothing else.
            'setup': self.env['pan.mail.setup'].answers(),
            'coverage': self.env['pan.mail.coverage'].counts_since(since),
            'rules': rules[:MAX_RULE_ENTRIES],
            'corrections': sum(r['corrected'] for r in rules),
            # What failed here in the last day, as codes and counts
            # (`pan.mail.error.CODES`): the server draws one `heartbeat_error`
            # per code, which is how a release that breaks sending for one
            # provider shows up the same day instead of in a ticket. The
            # `access.*` codes ride here too, so a mailbox nobody can send
            # from is a count on the day it is found; the server accepts no
            # key it does not know, so there is no boolean beside `sync_ok`
            # until mail-pro-admin takes one. A stale mailbox rides the same
            # way: `incoming.sync_stale`, counted per mailbox, is computed
            # here rather than recorded, because the cron that would record it
            # is the one not getting through.
            'errors': errors,
            # Where this Odoo runs: `sh`, `cloudpepper` or `self_hosted`,
            # read from the environment (`hosting.hosting`, odoo-mcp-pro-
            # admin#598). One word from a fixed list; the platform refuses
            # any other, and no host name or path ever rides with it.
            'hosting': hosting(),
        }
        if database_sees_a_copy(self.env):
            body['staging'] = True
        return body

    @api.model
    def _mail_counts_since(self, since):
        """`_mail_counts_between` from `since` until now."""
        return self._mail_counts_between(since, None)

    @api.model
    def _mails_by_day(self, now=None):
        """The last MAIL_DAYS UTC calendar days, today included, as
        `[{'day': 'YYYY-MM-DD', 'sent': n, 'received': n}]`, newest first.

        Each day is `[00:00, next 00:00)` in UTC with explicit bounds.
        `create_date` is stored in UTC, and a `read_group` on
        `create_date:day` would group in the user's timezone instead.
        """
        today = datetime.combine((now or fields.Datetime.now()).date(), time.min)
        days = []
        for back in range(MAIL_DAYS):
            start = today - timedelta(days=back)
            counts = self._mail_counts_between(start, start + timedelta(days=1))
            days.append({'day': start.date().isoformat(),
                         'sent': counts['outgoing'], 'received': counts['incoming']})
        return days

    @api.model
    def _mail_counts_between(self, start, end):
        """Mails sent and received through Mail Pro from `start` up to `end`
        (open-ended when None), as `{'incoming': n, 'outgoing': n}`. The meter the bill is computed from
        (odoo-mcp-pro-admin docs/plans/mail-pro-billing.md), so two rules:

        - **A mail counts on the day it reached Odoo** (`create_date`), not on
          the date it carries. Every outcome of the sync counts the same,
          threaded, created or left on a contact (#356); and a mailbox that
          catches up after a day offline counts its backlog on the day it
          lands, instead of on a day whose heartbeat had already gone out.
        - **A mailbox's first import does not count.** A mail dated before its
          mailbox existed came in because `sync_start_date` reached back into
          history, which is the customer bringing their past into Odoo, not
          mail they receive. A mail with no mailbox (one that was deleted
          since) counts, like every mail Odoo itself sent.
        """
        Message = self.env['mail.message'].sudo()
        base = [('x_direction', '!=', False), ('create_date', '>=', start)]
        if end is not None:
            base.append(('create_date', '<', end))
        scopes = [[('x_mailbox_id', '=', False)]]
        for mailbox in self.env['pan.mail.mailbox'].sudo().with_context(
                active_test=False).search([]):
            # Whole seconds: `date` is stored without them and the mailbox's
            # `create_date` with them, so a mail of the same second as its
            # mailbox would otherwise read as older than it.
            scopes.append([('x_mailbox_id', '=', mailbox.id),
                           ('date', '>=', mailbox.create_date.replace(microsecond=0))])
        counts = {'incoming': 0, 'outgoing': 0}
        for scope in scopes:
            for direction, count in Message._read_group(
                    base + scope, ['x_direction'], ['__count']):
                counts[direction] += count
        return counts

    # -------------------------------------------------------------------------
    # Signing in with the Pantalytics app
    # -------------------------------------------------------------------------

    @api.model
    def microsoft_start(self, return_url):
        """Ask our platform to start a Microsoft sign-in through the
        Pantalytics Mail Pro app (`pan.mail.provider.registration`).

        Sent under the installation key the heartbeat uses, so only a
        connected Odoo can start one. The answer is a single-use state (15
        minutes), the app's client id and the platform's redirect URI, where
        Microsoft answers and which sends the browser on to `return_url`.
        Nothing about the person or the mailbox goes out: the address is
        typed at Microsoft, and the code never passes through us.

        Called by whoever presses Connect, admin or not; the key is read as
        the system and never leaves this method.
        """
        from .pan_mail_provider import pantalytics_not_connected
        # A copy may be connected (#384), but a sign-in is a credential for a
        # live mailbox, and a copy holds none it may use.
        if database_is_copy(self.env):
            raise UserError(_('This Odoo instance is a copy (staging, a restored '
                              'backup), so no mailbox is signed in to here.'))
        link = self.current()
        key = encryption_utils.decrypt_link_secret(self.env, link.key_encrypted) \
            if link and link.key_encrypted else False
        if not key or link.status in ('not_connected', 'pending'):
            raise UserError(pantalytics_not_connected())
        code, body = link._post(MS_START_PATH, {'return_url': return_url}, key=key)
        if code == 200 and body.get('status') == 'ok':
            answer = {name: body.get(name) for name in ('state', 'client_id', 'redirect_uri')}
            if all(answer.values()) and str(answer['redirect_uri']).startswith('https://'):
                return answer
            _logger.warning('[License] The Microsoft start answered without %s',
                            ', '.join(name for name, value in answer.items() if not value)
                            or 'an https redirect URI')
            raise UserError(_('Pantalytics answered the Microsoft sign-in in a way '
                              'this version of Mail Pro does not understand. Try '
                              'again in a minute.'))
        _logger.warning('[License] The Microsoft start was refused: HTTP %s %s',
                        code, body.get('status'))
        if code == 401:
            raise UserError(_('Pantalytics no longer recognises the key of this '
                              'Odoo instance, so signing in with Microsoft cannot '
                              'start. An administrator reconnects it under '
                              'Settings, Mail Pro, step 1.'))
        if code == 503:
            raise UserError(_('Signing in with Microsoft is not available right '
                              'now. Try again later.'))
        raise UserError(_('Pantalytics could not start the Microsoft sign-in '
                          '(HTTP %s). Try again in a minute.') % code)

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
    @tools.ormcache()
    def _module_version(self):
        """The version of the module that is running, as the manifest says.

        Cached per registry: it rides every page load in `improve_config`,
        and the answer only changes when the code does, which is a restart
        (a new registry) or an upgrade (which clears the cache). A plain
        string, never a recordset, for the same reason as every ormcache.
        """
        return self.env['ir.module.module'].sudo().search(
            [('name', '=', 'pan_mail_pro')], limit=1).installed_version or ''

    def _notify(self, message, kind='info'):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'message': message, 'type': kind},
        }
