# -*- coding: utf-8 -*-
import logging

from odoo import http, _
from odoo.exceptions import UserError
from odoo.http import request

from ..models.mail_provider_client import (
    PROVIDER_CLIENTS,
    get_provider_client,
    oauth_redirect_uri,
)
from ..models.pan_mail_license import CONNECT_BACK_KEY
from ..models.pan_mail_provider import MS_APP_SESSION_KEY
from ..models.res_users import (
    INBOX_URL,
    SETTINGS_URL,
    SIGN_IN_BACK_KEY,
    SIGN_IN_NOTICE_KEY,
)

_logger = logging.getLogger(__name__)

# The one round trip in flight for this session, under this key: the nonce
# it must come back with, the mailbox it is for, where to land, and whether
# it was the silent try. In the session rather than on the user row, so the
# proof it produces is bound to the browser that asked for it.
VERIFY_SESSION_KEY = 'pan_mail_verify'

# What a silent sign-in (`prompt=none`) answers when it would have had to ask:
# both providers' words for "come back with a screen".
SILENT_REFUSALS = ('login_required', 'interaction_required', 'consent_required',
                   'account_selection_required')


def _safe_back(back, fallback=INBOX_URL):
    """A path on this Odoo, or `fallback`. Never another host: no scheme,
    no `//`, and no backslash, which a browser reads as a slash."""
    if back and back.startswith('/') and not back.startswith('//') and '\\' not in back:
        return back
    return fallback


def _sign_in_back():
    """Where the sign-in in flight started (`res.users._pan_mail_remember_back`),
    or, when nobody said: Settings for an administrator, the Inbox for
    everybody else."""
    home = SETTINGS_URL if request.env.user.has_group('base.group_system') else INBOX_URL
    return _safe_back(request.session.get(SIGN_IN_BACK_KEY), home)


def _result_page(success, title, message, action_url=None, action_label=None, back=None):
    """The page a sign-in round ends on when it did not work, or needs one
    more step. `action_url` is that step when there is one (approving the
    app for the organization, connecting after that approval); the way back
    is to the screen the round started from."""
    return request.render('pan_mail_pro.oauth_result', {
        'success': success, 'title': title, 'message': message,
        'action_url': action_url, 'action_label': action_label,
        'back_url': back or _sign_in_back(),
    })


class MailProConnectController(http.Controller):
    """One-click entry point for the "connect your mailbox" invitation.

    The invitation email cannot link to a button inside the Odoo client, so it
    links here instead: log in, and land straight on the provider's consent
    screen. A provider without one (IMAP/SMTP, whose credentials are typed in by
    an admin) sends the user to the settings page instead of to a redirect that
    does not exist.
    """

    @http.route('/mail_pro/connect', type='http', auth='user', website=True)
    def connect_mailbox(self, provider=None, verify=None, mailbox_id=None,
                        back=None, admin_consent=None, **kwargs):
        if verify:
            return self._start_verify(mailbox_id, back, silent=True)
        if admin_consent:
            return self._start_admin_consent()
        # `get_provider_client` hands back a *recordset*, and an empty one is
        # falsy — so `if not client` was true for every provider that exists and
        # this route redirected everybody to the settings page instead of to the
        # consent screen. Ask the registry whether the code is known, and the
        # client only what it knows: does it have a sign-in screen.
        provider = provider or request.env['pan.mail.provider'].current().provider
        if provider not in PROVIDER_CLIENTS:
            return request.redirect(SETTINGS_URL)
        if not get_provider_client(request.env, provider).uses_oauth:
            return request.redirect(SETTINGS_URL)

        # The banner says where it was pressed; a try again from a result
        # page keeps the start the round already had; the invitation mail
        # says nothing, and lands on the default.
        back = back or request.session.get(SIGN_IN_BACK_KEY) or False
        try:
            action = request.env.user.with_context(
                pan_mail_back=back).action_connect_mailbox(provider)
        except UserError as error:
            # A sentence (not connected to Pantalytics, the platform said no)
            # on the page the round would have ended on, not a traceback.
            return _result_page(False, _('Connection Failed'), str(error),
                                back=_safe_back(back, None))
        return request.redirect(action['url'], local=False)

    @staticmethod
    def _start_admin_consent():
        """Send a Microsoft 365 administrator to approve the Pantalytics app
        for the whole organization: the one step a tenant that does not let
        its users consent needs, once. Offered by the result page that says
        so; the answer comes back on `/mail_pro/microsoft/return`."""
        row = request.env['pan.mail.provider']._pantalytics_row('outlook')
        if not row or request.env.user.share:
            return request.redirect(SETTINGS_URL)
        try:
            url, _state = row._pantalytics_sign_in(admin_consent=True)
        except UserError as error:
            return _result_page(False, _('Approval not started'), str(error))
        return request.redirect(url, local=False)

    @classmethod
    def _start_verify(cls, mailbox_id, back, silent):
        """Send the owner of `mailbox_id` to their provider's sign-in, to
        come back with the provider's word that it is them.

        The silent try first (`prompt=none`): a browser already signed in at
        the provider as this address comes straight back, and one that is
        not is sent again with a screen (`_finish_verify`). The address is
        the hint, the stable id is what is compared on the way back.
        """
        Conversation = request.env['pan.mail.conversation']
        mailbox = Conversation._own_mailbox(mailbox_id, verified=False)
        client = mailbox._get_client()
        back = _safe_back(back)
        if not client.uses_oauth:
            # A password provider is confirmed from the Inbox itself.
            return request.redirect(back)
        account = Conversation._live_account(mailbox)
        prompt = 'none' if silent else ''
        # Through the app that issued the account's token: after a move off
        # the customer's own app registration, an account that has not
        # reconnected yet is confirmed by the app it already consented to,
        # not sent to a consent screen for ours in the middle of reading.
        app_row = request.env['pan.mail.provider']._pantalytics_row(mailbox.provider)
        if app_row and account.registration == 'own':
            app_row = app_row.browse()
        if app_row:
            # Through the Pantalytics app the platform issues the state, and
            # Microsoft answers on its redirect URI, which hands the round to
            # `/mail_pro/microsoft/return` and from there to the callback.
            try:
                url, state = app_row._pantalytics_sign_in(
                    login_hint=account.email, prompt=prompt)
            except UserError as error:
                return _result_page(False, _('Not confirmed'), str(error))
        else:
            state = client.generate_oauth_state()
            url = client.get_authorization_url(
                oauth_redirect_uri(request.env, mailbox.provider), state=state,
                login_hint=account.email, prompt=prompt, registration='own')
        request.session[VERIFY_SESSION_KEY] = {
            'state': state, 'mailbox_id': mailbox.id, 'back': back,
            'silent': bool(silent), 'provider': mailbox.provider,
        }
        return request.redirect(url, local=False)


class MailProOAuthController(http.Controller):
    """Where every provider's consent screen sends the browser back to.

    One implementation, two routes. The paths are fixed strings because they are
    registered in each customer's Azure and Google console - they are public API
    and cannot be derived - but nothing behind them is provider-specific: the
    client exchanges the code, names the address it authorized, and the account
    stores what came back.

    Splitting this in two is what let the two copies drift apart: only Microsoft
    logged which identity had been connected, and only Google kept a refresh
    token Google had declined to re-issue. Neither difference was a decision.
    """

    @http.route('/microsoft_oauth/callback', type='http', auth='user', website=True)
    def microsoft_callback(self, **kwargs):
        return self._handle_callback('outlook', **kwargs)

    @http.route('/google_oauth/callback', type='http', auth='user', website=True)
    def google_callback(self, **kwargs):
        return self._handle_callback('gmail', **kwargs)

    @http.route('/mail_pro/microsoft/return', type='http', auth='user', website=True)
    def microsoft_return(self, **kwargs):
        """Microsoft's answer to a sign-in through the Pantalytics app.

        Microsoft answers on the platform's redirect URI, which sends the
        browser on here with only code, state, error, error_description,
        admin_consent and tenant. The state has to be the one this session
        started (`MS_APP_SESSION_KEY`) and is good once; after that it is
        the same callback as every other sign-in, with the platform's
        redirect URI and this session's PKCE verifier in place of a secret.
        """
        pending = request.session.get(MS_APP_SESSION_KEY) or {}
        state = kwargs.get('state')
        if not state or not pending.get('state') or state != pending['state']:
            _logger.error('[OAuth] Pantalytics app state validation failed for user %s',
                          request.env.user.id)
            return _result_page(False, _('Connection Failed'),
                                _('Security validation failed. Please try connecting again.'))
        del request.session[MS_APP_SESSION_KEY]
        if pending.get('admin_consent'):
            return self._admin_consent_answer(kwargs)
        return self._handle_callback('outlook', sign_in=pending, **kwargs)

    @staticmethod
    def _admin_consent_answer(kwargs):
        """What an administrator's approval for the organization came back as."""
        if kwargs.get('error') or str(kwargs.get('admin_consent')).lower() != 'true':
            _logger.warning('[OAuth] The organization approval was not given: %s - %s',
                            kwargs.get('error'), kwargs.get('error_description'))
            return _result_page(False, _('Not approved'), _(
                'Microsoft did not approve Mail Pro for your organization: %s')
                % (kwargs.get('error_description') or kwargs.get('error') or _('no answer')))
        _logger.info('[OAuth] Mail Pro approved for Microsoft tenant %s', kwargs.get('tenant'))
        return _result_page(True, _('Approved'), _(
            'Mail Pro is approved for your organization. Everybody can connect '
            'their mailbox now.'), action_url='/mail_pro/connect',
            action_label=_('Connect your mailbox'))

    def _handle_callback(self, provider, sign_in=None, **kwargs):
        """`sign_in` is the session's record of a round through the
        Pantalytics app, already matched on its state by `microsoft_return`:
        its redirect URI and PKCE verifier stand in for the registered
        callback and the client secret."""
        user = request.env.user
        client = get_provider_client(request.env, provider)

        if user.share:
            _logger.warning('[OAuth] Portal user %s reached the %s callback', user.id, provider)
            return _result_page(False, _('Connection Failed'),
                                _('Only internal users connect a mailbox.'))

        # A confirm-it's-you round comes back through the same registered
        # URL and is told apart by its nonce, which lives in the session and
        # is good once. Checked before the error branch: a silent try that
        # was refused is an error here and the reason to try again with a
        # screen.
        pending = request.session.get(VERIFY_SESSION_KEY)
        if pending and kwargs.get('state') and kwargs['state'] == pending.get('state') \
                and pending.get('provider') == provider:
            del request.session[VERIFY_SESSION_KEY]
            return self._finish_verify(provider, client, pending, kwargs, sign_in)

        error = kwargs.get('error')
        if error:
            _logger.error('[OAuth] %s returned %s - %s',
                          provider, error, kwargs.get('error_description'))
            if sign_in and client.needs_admin_consent(error, kwargs.get('error_description')):
                # The tenant wants an administrator to approve the app first.
                # Once, for everybody: the page offers that step rather than
                # Microsoft's code.
                return _result_page(False, _('Approval needed'), _(
                    'Your Microsoft 365 organization asks an administrator to '
                    'approve Mail Pro once. Are you that administrator? Approve '
                    'it now. If not, ask them to sign in to Odoo and press '
                    'Connect, then approve.'),
                    action_url='/mail_pro/connect?admin_consent=1',
                    action_label=_('Approve for the organization'))
            return _result_page(False, _('Connection Failed'),
                                f"{error}: {kwargs.get('error_description')}")

        # CSRF: the nonce we handed out must come back, and is good once. A
        # round through the Pantalytics app was matched against the session
        # before it got here, and never wrote a nonce on this row.
        if not sign_in:
            stored_state = user.sudo().x_pan_mail_oauth_state
            received_state = kwargs.get('state')
            if not received_state or not stored_state or received_state != stored_state:
                _logger.error('[OAuth] CSRF state validation failed for user %s', user.id)
                return _result_page(False, _('Connection Failed'),
                                    _('Security validation failed. Please try connecting again.'))
            user.sudo().write({'x_pan_mail_oauth_state': False})

        code = kwargs.get('code')
        if not code:
            _logger.error('[OAuth] No authorization code received from %s', provider)
            return _result_page(False, _('Connection Failed'),
                                _('No authorization code received.'))

        try:
            tokens = self._exchange(client, provider, code, sign_in)
            identity = client.read_user_info(
                tokens['access_token'], id_token=tokens.get('id_token'))
            email = identity.get('email')
            if not email:
                # Every client answers a failed /me with no address rather
                # than raising, and the account's "connected as A, consented
                # as B" guard compares addresses: with none to compare, B's
                # tokens would land on A's row unchallenged. No address, no
                # connection -- refused here, before anything is stored, and
                # the outer except turns it into a ledger row like any other
                # failed callback.
                raise UserError(_(
                    'The provider did not say which address was authorized, '
                    'so nothing was stored. Try connecting again.'))

            # A sign-in the registration cannot serve -- another tenant, a
            # consumer Google account -- is refused with the reason now,
            # rather than at the first send with a 404 that reads like a
            # missing right. Its own ledger row, so the heartbeat can count
            # it apart from a callback that merely broke.
            refusal = client.identity_refusal(identity, registration=self._registration(sign_in))
            if refusal:
                _logger.warning('[OAuth] Refused %s sign-in %s for Odoo user %s: %s',
                                provider, email, user.login, refusal)
                request.env['pan.mail.error']._record(
                    'oauth.tenant_mismatch', level='warning', detail=refusal)
                return _result_page(False, _('Connection Failed'), refusal)

            account = request.env['pan.mail.account'].sudo()._store_tokens(
                provider, user, email,
                tokens['access_token'], tokens.get('refresh_token'), tokens['token_expiry'],
                identity=identity, scopes=tokens.get('scope'),
                registration=self._registration(sign_in),
            )
            _logger.info('[OAuth] Connected %s account %s for Odoo user %s',
                         provider, email, user.login)
            # A user with no address on the record (a fresh Odoo.sh admin)
            # gets the one the provider just vouched for. One that is there
            # stays, whatever it says: "Signed in as" shows a difference, and
            # nothing here enforces one (3 Nov).
            if not user.email:
                user.sudo().email = email

            # Every mailbox this sign-in serves, checked now: the person who
            # just consented reads the answer on the screen they land on,
            # not an hour later off the cron. Its own savepoint, and
            # `_verify_access` raises nothing of its own.
            with request.env.cr.savepoint():
                request.env['pan.mail.mailbox'].sudo()._verify_for_account(account)

            # The credentials are the point; the mailbox is a convenience. A
            # claim that fails (the internal domains are not set yet, so the
            # mailbox constraint refuses) must neither turn a successful
            # connection into "Connection Failed" nor leave a half-created
            # row behind: Odoo validates after the INSERT, and a swallowed
            # exception would commit it, past the very gate that refused.
            try:
                with request.env.cr.savepoint():
                    self._retry_error_mailboxes(user, provider)
                    setup = request.env['pan.mail.setup']
                    setup.propose_internal_domain(user, email)
                    if not request.env['pan.mail.domain'].configuration_error():
                        self._claim_personal_mailbox(user, provider, email)
                        setup.propose_notification_mailbox(user, email)
            except Exception as exception:  # noqa: BLE001 - recorded, the account stays
                _logger.exception('[OAuth] Connected %s, but its mailbox could not be '
                                  'claimed yet', email)
                # A warning: the credentials are in, the mailbox is not, and
                # a person who reads "connected" on the result page and then
                # finds no mailbox needs the reason somewhere. The ledger is
                # that somewhere; the page says only that there is one.
                request.env['pan.mail.error']._record(
                    'oauth.callback_failed', exception, level='warning')
                return self._signed_in('warning', _(
                    'Signed in as %s. Your mailbox is not set up yet. An '
                    'administrator can add it in Settings, Mail Pro.') % email)

            return self._signed_in('success', _('Signed in as %s') % email)

        except Exception as exception:
            _logger.exception('[OAuth] Failed to handle the %s callback', provider)
            request.env['pan.mail.error']._record('oauth.callback_failed', exception)
            return _result_page(False, _('Connection Failed'), str(exception))

    @staticmethod
    def _signed_in(kind, message):
        """No page: straight back to the screen the sign-in started from,
        which says who signed in once it has loaded (`ir.http.session_info`
        hands the line to the webclient, `connect_banner.js` shows it). The
        banner and the reconnect nudge are gone by then on their own, since
        they read the account that was just stored."""
        back = _sign_in_back()
        request.session.pop(SIGN_IN_BACK_KEY, None)
        request.session[SIGN_IN_NOTICE_KEY] = {'type': kind, 'message': message}
        return request.redirect(back)

    @staticmethod
    def _registration(sign_in):
        """Whose app the round went through: the Pantalytics app when it came
        back through `/mail_pro/microsoft/return`, the customer's own when it
        came back to the callback registered in their console. Said by the
        round, not by the provider row, which may have switched apps while
        the round was out."""
        return 'pantalytics' if sign_in else 'own'

    @classmethod
    def _exchange(cls, client, provider, code, sign_in):
        """The code exchange: at the registered callback with the secret, or,
        through the Pantalytics app, at the platform's redirect URI with this
        session's PKCE verifier. Neither the code nor the tokens are logged."""
        if sign_in:
            return client._exchange_code_for_tokens(
                code, sign_in['redirect_uri'], code_verifier=sign_in.get('code_verifier'),
                registration=cls._registration(sign_in))
        return client._exchange_code_for_tokens(
            code, oauth_redirect_uri(request.env, provider),
            registration=cls._registration(sign_in))

    def _finish_verify(self, provider, client, pending, kwargs, sign_in=None):
        """The provider's answer to "is this the person behind the mailbox".

        Nothing is written to the account either way: the tokens of this
        round are dropped, and the one thing that changes on a yes is the
        session. A different stable id than the account's is the refusal
        this exists for (an administrator with the owner's Odoo session and
        their own Microsoft account), and it says so with both addresses.
        """
        back = _safe_back(pending.get('back'))
        error = kwargs.get('error')
        if error:
            if pending.get('silent') and error in SILENT_REFUSALS:
                return MailProConnectController._start_verify(
                    pending.get('mailbox_id'), back, silent=False)
            _logger.warning('[OAuth] %s refused the identity check: %s - %s',
                            provider, error, kwargs.get('error_description'))
            return _result_page(False, _('Not confirmed'), _(
                '%s did not confirm the sign-in. Try again from the Inbox.')
                % client.provider_label(), back=back)
        code = kwargs.get('code')
        if not code:
            return _result_page(False, _('Not confirmed'), _(
                '%s sent no sign-in back. Try again from the Inbox.')
                % client.provider_label(), back=back)
        Conversation = request.env['pan.mail.conversation']
        try:
            mailbox = Conversation._own_mailbox(pending.get('mailbox_id'), verified=False)
            account = Conversation._live_account(mailbox)
            tokens = self._exchange(client, provider, code, sign_in)
            identity = client.read_user_info(
                tokens['access_token'], id_token=tokens.get('id_token'))
        except Exception as exception:
            _logger.exception('[OAuth] The %s identity check broke', provider)
            request.env['pan.mail.error']._record('oauth.callback_failed', exception)
            return _result_page(False, _('Not confirmed'), str(exception), back=back)
        if not self._same_sign_in(account, identity):
            _logger.warning('[OAuth] %s confirmed %s, not the %s behind mailbox %s',
                            provider, identity.get('email'), account.email, mailbox.id)
            return _result_page(False, _('Not confirmed'), _(
                'That sign-in is %(theirs)s. Sign in as %(ours)s to read this '
                'mailbox here. Nothing changed.',
                theirs=identity.get('email') or _('another account'),
                ours=account.email), back=back)
        Conversation._mark_session_verified(account)
        _logger.info('[OAuth] %s confirmed %s for this session', provider, account.email)
        return request.redirect(back)

    @staticmethod
    def _same_sign_in(account, identity):
        """The stable id when both sides have one (a grant stored before
        19.0.28 may not), else the address, case aside."""
        theirs = identity.get('provider_user_id')
        if account.provider_user_id and theirs:
            return account.provider_user_id == theirs
        email = identity.get('email')
        return bool(email) and email.lower() == (account.email or '').lower()

    def _retry_error_mailboxes(self, user, provider):
        """A mailbox that failed for want of a token deserves another go."""
        mailboxes = request.env['pan.mail.mailbox'].sudo().search([
            ('owner_user_id', '=', user.id),
            ('provider', '=', provider),
            ('state', '=', 'error'),
        ])
        if mailboxes:
            mailboxes.write({'state': 'draft', 'error_message': False})
            _logger.info('[OAuth] Reset %s mailbox(es) from error to draft', len(mailboxes))

    def _claim_personal_mailbox(self, user, provider, email):
        """Give the user the personal mailbox for the address they just authorized.

        Creating it here is what makes "connect" a single click: the address is
        the one the provider just told us about, so there is nothing left to ask.
        An address that already has a mailbox is never repurposed - it may be a
        shared mailbox somebody configured deliberately - only an unowned
        personal one is claimed.
        """
        if not email:
            return

        Mailbox = request.env['pan.mail.mailbox'].sudo()
        # Archived rows included: the address is unique across them, so an
        # archived mailbox would make the create below fail on its constraint.
        existing = Mailbox.with_context(active_test=False).search(
            [('email', '=ilike', email)], limit=1)
        if not existing:
            # Personal by construction: the account created a moment ago
            # carries this very address, which is what the type is derived from.
            mailbox = Mailbox.create({
                'email': email,
                'provider': provider,
                'owner_user_id': user.id,
            })
            user.sudo().write({'x_default_mailbox_id': mailbox.id})
            _logger.info('[OAuth] Created personal mailbox %s for %s', email, user.login)
        elif existing.mailbox_type == 'personal' and existing.owner_user_id == user:
            # This user's own archived one: the consent that just happened is
            # the reason it exists, so it comes back.
            existing.write({'owner_user_id': user.id, 'active': True})
            _logger.info('[OAuth] Assigned existing mailbox %s to %s', email, user.login)


class MailProPantalyticsController(http.Controller):
    """Where the Pantalytics approval page sends the admin back to.

    A redirect from our site after Approve, so this is not an OAuth callback
    and nothing about it is registered anywhere. It collects the key and lands
    where Connect was pressed (`pan.mail.license._remember_back`): the Inbox
    for its gate, Settings for Settings' button and for anybody whose session
    does not say.
    """

    @http.route('/mail_pro/pantalytics/return', type='http', auth='user')
    def pantalytics_return(self, **kwargs):
        if request.env.user.has_group('base.group_system'):
            link = request.env['pan.mail.license'].current()
            if link:
                link.collect_on_return()
        back = request.session.pop(CONNECT_BACK_KEY, None)
        return request.redirect(_safe_back(back, SETTINGS_URL))
