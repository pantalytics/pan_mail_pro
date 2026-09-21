# -*- coding: utf-8 -*-
import logging

from odoo import http, _
from odoo.exceptions import UserError, ValidationError
from odoo.http import request

from ..models.mail_provider_client import (
    PROVIDER_CLIENTS,
    get_provider_client,
    get_setup_provider,
    oauth_redirect_uri,
)

_logger = logging.getLogger(__name__)

# The Mail Pro page of Odoo's settings. The fragment is the `name` of the
# `<app>` element in `res_config_settings_views.xml`; anything else lands on
# the general settings page with no sign that a jump was intended.
SETTINGS_URL = '/odoo/settings#pan_mail_pro'


# What each rung means for the people around you, in the words of the person
# reading it. The labels on the field say what Odoo does; these say what a
# colleague ends up seeing, which is the half that decides the answer.
def consent_seen():
    """Built per call, so `_()` runs in the reader's language.

    A module-level dict would be English forever: the constant is evaluated at
    import time, and splicing it into a translated sentence leaves the half
    that carries the meaning untranslated.
    """
    return {
        'replies': _('answers to mail Odoo sent'),
        'both': _('those answers, including the ones you write in your mail app'),
        'contacts': _('that, plus new mail from contacts Odoo already knows'),
        'everyone': _('that, plus new mail from anyone'),
    }


def _consent_page(mailbox):
    """The one screen where somebody chooses what Odoo reads from their mail."""
    seen = consent_seen()
    levels = [
        (code, label, seen.get(code, ''))
        for code, label in mailbox._fields['sync_level'].selection
    ]
    return request.render('pan_mail_pro.oauth_consent', {
        'mailbox': mailbox,
        'levels': levels,
        'current_seen': seen.get(mailbox.sync_level or 'replies', ''),
    })


def _result_page(success, title, message):
    return request.render('pan_mail_pro.oauth_result', {
        'success': success, 'title': title, 'message': message,
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
    def connect_mailbox(self, provider=None, **kwargs):
        # `get_provider_client` hands back a *recordset*, and an empty one is
        # falsy — so `if not client` was true for every provider that exists and
        # this route redirected everybody to the settings page instead of to the
        # consent screen. Ask the registry whether the code is known, and the
        # client only what it knows: does it have a sign-in screen.
        provider = provider or get_setup_provider(request.env)
        if provider not in PROVIDER_CLIENTS:
            return request.redirect(SETTINGS_URL)
        if not get_provider_client(request.env, provider).uses_oauth:
            return request.redirect(SETTINGS_URL)

        action = request.env.user.action_connect_mailbox(provider)
        return request.redirect(action['url'], local=False)


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

    def _handle_callback(self, provider, **kwargs):
        user = request.env.user
        client = get_provider_client(request.env, provider)

        if user.share:
            _logger.warning('[OAuth] Portal user %s reached the %s callback', user.id, provider)
            return _result_page(False, _('Connection Failed'),
                                _('Only internal users connect a mailbox.'))

        error = kwargs.get('error')
        if error:
            _logger.error('[OAuth] %s returned %s - %s',
                          provider, error, kwargs.get('error_description'))
            return _result_page(False, _('Connection Failed'),
                                f"{error}: {kwargs.get('error_description')}")

        # CSRF: the nonce we handed out must come back, and is good once.
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
            tokens = client._exchange_code_for_tokens(
                code, oauth_redirect_uri(request.env, provider))
            email = client.get_user_email(tokens['access_token'])

            request.env['pan.mail.account'].sudo()._store_tokens(
                provider, user, email,
                tokens['access_token'], tokens.get('refresh_token'), tokens['token_expiry'],
            )
            _logger.info('[OAuth] Connected %s account %s for Odoo user %s',
                         provider, email, user.login)

            # The credentials are the point; the mailbox is a convenience. A
            # claim that fails (the internal domains are not set yet, so the
            # mailbox constraint refuses) must neither turn a successful
            # connection into "Connection Failed" nor leave a half-created
            # row behind: Odoo validates after the INSERT, and a swallowed
            # exception would commit it, past the very gate that refused.
            try:
                with request.env.cr.savepoint():
                    self._retry_error_mailboxes(user, provider)
                    if not request.env['pan.mail.domain'].configuration_error():
                        self._claim_personal_mailbox(user, provider, email)
            except Exception:
                _logger.exception('[OAuth] Connected %s, but its mailbox could not be '
                                  'claimed yet', email)

            mailbox = self._own_mailbox(user, email)
            if mailbox:
                # The claim worked, so there is something to consent about.
                # Without one (internal domains still unset, a shared address
                # somebody else configured) there is nothing to ask yet.
                return _consent_page(mailbox)
            return _result_page(True, _('Mailbox Connected'),
                                _('Your email account has been connected successfully.'))

        except Exception as exception:
            _logger.exception('[OAuth] Failed to handle the %s callback', provider)
            return _result_page(False, _('Connection Failed'), str(exception))

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

    def _own_mailbox(self, user, email):
        """This user's own personal mailbox for the address they authorized."""
        if not email:
            return request.env['pan.mail.mailbox']
        mailbox = request.env['pan.mail.mailbox'].sudo().search(
            [('email', '=ilike', email), ('owner_user_id', '=', user.id)], limit=1)
        if mailbox.mailbox_type != 'personal' or mailbox.is_notification_mailbox:
            return request.env['pan.mail.mailbox']
        return mailbox

    @http.route('/mail_pro/consent', type='http', auth='user', website=True,
                methods=['POST'])
    def set_consent(self, mailbox_id=None, level=None, **kwargs):
        """Store the level its owner just picked.

        The route is the boundary, not the form: an internal user has no write
        access to `pan.mail.mailbox` at all, so the write is a sudo and this
        check is the only thing between it and somebody else's mailbox.
        """
        user = request.env.user
        mailbox = request.env['pan.mail.mailbox'].sudo().browse(
            int(mailbox_id) if str(mailbox_id or '').isdigit() else 0).exists()
        valid = dict(request.env['pan.mail.mailbox']._fields['sync_level'].selection)
        if not mailbox or mailbox.owner_user_id != user or level not in valid:
            _logger.warning('[OAuth] Refused a consent write by %s for mailbox %s',
                            user.login, mailbox_id)
            return _result_page(False, _('Not Saved'),
                                _('That mailbox is not yours to change.'))
        try:
            # A savepoint, not a bare try: a constraint that fires after the
            # UPDATE leaves the new value in the transaction, and swallowing
            # the error would commit exactly the write that was refused.
            with request.env.cr.savepoint():
                mailbox.write({'sync_level': level})
        except (UserError, ValidationError) as error:
            # A mailbox can refuse to read more for reasons that have nothing
            # to do with this person: no notification mailbox yet, no internal
            # domains. The refusal is a sentence on this page, not a 422 in the
            # tab a provider just handed back.
            _logger.info('[OAuth] %s could not set %s to %s: %s',
                         user.login, mailbox.email, level, error)
            return _result_page(False, _('Not Saved'), str(error))
        _logger.info('[OAuth] %s set %s to sync level %s', user.login, mailbox.email, level)
        return _result_page(True, _('Mailbox Connected'), _(
            'Odoo now reads %(what)s from your mailbox. You can change that '
            'in My Preferences.', what=consent_seen().get(level, '')))


class MailProPantalyticsController(http.Controller):
    """Where the Pantalytics approval page sends the admin back to.

    A plain link the admin clicks on our site after approving, so this is not an
    OAuth callback and nothing about it is registered anywhere. It collects the
    key and lands on the settings page, which shows the outcome.
    """

    @http.route('/mail_pro/pantalytics/return', type='http', auth='user')
    def pantalytics_return(self, **kwargs):
        if request.env.user.has_group('base.group_system'):
            link = request.env['pan.mail.license'].current()
            if link:
                link.collect_on_return()
        return request.redirect(SETTINGS_URL)
